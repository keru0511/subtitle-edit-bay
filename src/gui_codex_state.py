from __future__ import annotations

import threading
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable, Mapping, cast

from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import (
    AgentMessageDeltaNotification,
    TurnCompletedNotification,
    TurnStartedNotification,
)
from openai_codex.models import JsonObject, Notification

from .codex_isolation import (
    CodexIsolationError,
    build_isolated_thread_params,
    collect_mcp_server_names,
    isolated_codex_cwd,
)
from .codex_runtime import redact_codex_diagnostic
from .codex_sdk import create_codex_client, run_structured_turn, sdk_deadline
from .data_boundary import coerce_float, decode_json, is_object_list, is_object_mapping

if TYPE_CHECKING:
    from .codex_edit_proposal import EditProposalApplyResult


CODEX_SCOPES = ("selected", "current", "time_range", "all")
CODEX_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "operations", "warnings"],
    "properties": {
        "summary": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "operations": {"type": "array"},
    },
}
_CONTEXT_FIELDS = (
    "id",
    "start",
    "end",
    "text",
    "speaker",
    "emphasis",
    "position",
    "subtitle_line_count",
    "subtitle_font_scale",
    "subtitle_font_family",
)


ClientFactory = Callable[[str | None], CodexClient]


class CodexSessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexSessionSnapshot:
    state: str = "disabled"
    thread_id: str = ""
    turn_id: str = ""
    revision: int = 0
    error: str = ""
    message: str = ""
    proposal: Mapping[str, object] | None = None


def build_codex_context(
    project: Mapping[str, object],
    scope: str,
    *,
    selected_segment_ids: set[str] | None = None,
    current_time: float | None = None,
    range_start: float | None = None,
    range_end: float | None = None,
) -> dict[str, object]:
    """Build a safe, path-free context payload for a Codex turn."""
    if scope not in CODEX_SCOPES:
        raise ValueError(f"unknown Codex scope: {scope}")
    raw_segments = project.get("segments", [])
    if not is_object_list(raw_segments):
        raise ValueError("project segments must be a list")
    segments = [item for item in raw_segments if is_object_mapping(item)]
    if scope == "selected":
        selected = selected_segment_ids or set()
        segments = [item for item in segments if str(item.get("id")) in selected]
    elif scope == "current":
        if current_time is None:
            raise ValueError("current scope requires current_time")
        segments = [
            item
            for item in segments
            if coerce_float(item.get("start", 0.0)) <= current_time <= coerce_float(item.get("end", 0.0))
        ]
    elif scope == "time_range":
        if range_start is None or range_end is None or range_end < range_start:
            raise ValueError("time_range requires a valid range")
        segments = [
            item
            for item in segments
            if coerce_float(item.get("end", 0.0)) > range_start and coerce_float(item.get("start", 0.0)) < range_end
        ]
    safe_segments = [{field: item[field] for field in _CONTEXT_FIELDS if field in item} for item in segments]
    raw_subtitle_settings = project.get("subtitle_settings", {})
    subtitle_settings = raw_subtitle_settings if is_object_mapping(raw_subtitle_settings) else {}
    return {
        "scope": scope,
        "segment_count": len(safe_segments),
        "segments": safe_segments,
        "subtitle_settings": {
            key: subtitle_settings.get(key)
            for key in ("font_size", "outline_color", "outline_thickness")
            if key in subtitle_settings
        },
    }


class CodexSessionController:
    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        proposal_parser: Callable[[Mapping[str, object]], object] | None = None,
        on_state: Callable[[CodexSessionSnapshot], None] | None = None,
        on_message: Callable[[str], None] | None = None,
        on_proposal: Callable[[Mapping[str, object]], None] | None = None,
        callback_dispatcher: Callable[[Callable[[], None]], None] | None = None,
        isolated_turn: bool = False,
    ) -> None:
        self.client_factory: ClientFactory = client_factory or self._default_client_factory
        self.proposal_parser: Callable[[Mapping[str, object]], object] = (
            proposal_parser or self._default_proposal_parser
        )
        self.on_state = on_state
        self.on_message = on_message
        self.on_proposal = on_proposal
        self._callback_dispatcher = callback_dispatcher or (lambda callback: callback())
        self.isolated_turn = bool(isolated_turn)
        self._snapshot = CodexSessionSnapshot()
        self._client: CodexClient | None = None
        self._thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._generation = 0
        self._stop_event = threading.Event()

    @property
    def snapshot(self) -> CodexSessionSnapshot:
        return self._snapshot

    @property
    def running(self) -> bool:
        return self._snapshot.state in {"starting", "authenticating", "running"}

    def start(
        self,
        *,
        prompt: str,
        context: Mapping[str, object],
        output_schema: Mapping[str, object] | None = None,
        revision: int = 0,
    ) -> None:
        if self.running or (self._thread is not None and self._thread.is_alive()):
            raise CodexSessionError("Codex turn is already running")
        if not str(prompt).strip():
            raise CodexSessionError("prompt must not be empty")
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            stop_event = threading.Event()
            self._stop_event = stop_event
        self._publish(
            CodexSessionSnapshot(state="starting", revision=revision),
            generation=generation,
            stop_event=stop_event,
        )
        turn_context: dict[str, object] = dict(context)
        turn_output_schema: dict[str, object] = dict(output_schema or CODEX_OUTPUT_SCHEMA)

        def worker() -> None:
            self._run(
                prompt=prompt,
                context=turn_context,
                output_schema=turn_output_schema,
                revision=revision,
                generation=generation,
                stop_event=stop_event,
            )

        self._thread = threading.Thread(target=worker, name="codex-edit-session", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._state_lock:
            stop_event = self._stop_event
            generation = self._generation
            client = self._client
            snapshot = self._snapshot
        stop_event.set()
        if client is not None and snapshot.turn_id:
            try:
                with sdk_deadline(client):
                    client.turn_interrupt(snapshot.thread_id, snapshot.turn_id)
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if snapshot.state in {"starting", "authenticating", "running"}:
            self._publish(
                CodexSessionSnapshot(
                    state="stopped",
                    thread_id=snapshot.thread_id,
                    turn_id=snapshot.turn_id,
                    revision=snapshot.revision,
                    message="Codex編集を停止しました",
                ),
                generation=generation,
            )

    def apply_to_project(
        self,
        project: Mapping[str, object],
        proposal: object,
        *,
        selected_operation_ids: set[str] | None = None,
        current_revision: int | None = None,
    ) -> EditProposalApplyResult:
        from .codex_edit_proposal import apply_edit_proposal

        return apply_edit_proposal(
            project,
            proposal,
            selected_operation_ids=selected_operation_ids,
            current_revision=current_revision,
        )

    def _run(
        self,
        *,
        prompt: str,
        context: Mapping[str, object],
        output_schema: Mapping[str, object],
        revision: int,
        generation: int,
        stop_event: threading.Event,
    ) -> None:
        client: CodexClient | None = None
        try:
            workspace_context = isolated_codex_cwd() if self.isolated_turn else nullcontext(None)
            with workspace_context as isolated_cwd:
                client = self._create_client(isolated_cwd)
                with self._state_lock:
                    if not self._is_active(generation, stop_event):
                        return
                    self._client = client
                with sdk_deadline(client):
                    client.start()
                    client.initialize()
                if not self._is_active(generation, stop_event):
                    return
                self._publish(
                    CodexSessionSnapshot(state="authenticating", revision=revision),
                    generation=generation,
                    stop_event=stop_event,
                )
                with sdk_deadline(client):
                    account = client.account_read()
                if not self._is_active(generation, stop_event):
                    return
                if account.account is None and account.requires_openai_auth:
                    self._publish(
                        CodexSessionSnapshot(
                            state="unauthenticated",
                            revision=revision,
                            error="Codexへログインしてください",
                        ),
                        generation=generation,
                        stop_event=stop_event,
                    )
                    return
                thread_params: JsonObject = {
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                }
                if self.isolated_turn:
                    try:
                        if isolated_cwd is None:
                            raise CodexIsolationError("Codex turn working directory is unavailable")
                        configured_mcp_names = collect_mcp_server_names(
                            client,
                            config_only=True,
                        )
                        thread_params = build_isolated_thread_params(
                            isolated_cwd,
                            mcp_server_names=configured_mcp_names,
                        )
                    except (AttributeError, CodexIsolationError) as error:
                        raise CodexSessionError("Codexの隔離設定を確認できません") from error
                with sdk_deadline(client):
                    thread = client.thread_start(thread_params)
                if not self._is_active(generation, stop_event):
                    return
                thread_id = thread.thread.id
                if self.isolated_turn:
                    try:
                        exposed_mcp_names = collect_mcp_server_names(
                            client,
                            thread_id=thread_id,
                        )
                    except (AttributeError, CodexIsolationError) as error:
                        raise CodexSessionError("Codexの隔離状態を確認できません") from error
                    if exposed_mcp_names:
                        raise CodexSessionError("Codexの提案turnにMCPが公開されています")
                self._publish(
                    CodexSessionSnapshot(state="running", thread_id=thread_id, revision=revision),
                    generation=generation,
                    stop_event=stop_event,
                )
                response = run_structured_turn(
                    client,
                    thread_id=thread_id,
                    prompt=prompt,
                    context=context,
                    output_schema=output_schema,
                    cwd=isolated_cwd,
                    on_event=lambda event: self._on_notification(generation, stop_event, event),
                )
                if not self._is_active(generation, stop_event):
                    return
                raw_proposal = response.get("proposal", response.get("output", response))
                if isinstance(raw_proposal, str):
                    raw_proposal = decode_json(raw_proposal)
                if not is_object_mapping(raw_proposal) or not all(isinstance(key, str) for key in raw_proposal):
                    raise CodexSessionError("Codex output is not a proposal object")
                proposal_data = cast(Mapping[str, object], raw_proposal)
                self.proposal_parser(proposal_data)
                proposal = dict(proposal_data)
                self._publish(
                    CodexSessionSnapshot(
                        state="proposal_ready",
                        thread_id=thread_id,
                        revision=revision,
                        message=self._snapshot.message,
                        proposal=proposal,
                    ),
                    generation=generation,
                    stop_event=stop_event,
                )
                on_proposal = self.on_proposal
                if on_proposal is not None:
                    self._dispatch(
                        lambda: on_proposal(proposal),
                        generation,
                        stop_event,
                    )
        except Exception as error:
            if self._is_current_generation(generation) and not stop_event.is_set():
                self._publish(
                    CodexSessionSnapshot(
                        state="error",
                        thread_id=self._snapshot.thread_id,
                        turn_id=self._snapshot.turn_id,
                        revision=revision,
                        error=redact_codex_diagnostic(error),
                    ),
                    generation=generation,
                    stop_event=stop_event,
                )
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
                with self._state_lock:
                    if self._client is client:
                        self._client = None

    def _create_client(self, cwd: str | None) -> CodexClient:
        return self.client_factory(cwd)

    def _on_notification(
        self,
        generation: int,
        stop_event: threading.Event,
        notification: Notification,
    ) -> None:
        if not self._is_active(generation, stop_event):
            return
        payload = notification.payload
        if isinstance(payload, (TurnStartedNotification, TurnCompletedNotification)):
            self._publish(
                replace(self._snapshot, turn_id=payload.turn.id), generation=generation, stop_event=stop_event
            )
        if isinstance(payload, AgentMessageDeltaNotification):
            self._publish(
                replace(self._snapshot, turn_id=payload.turn_id, message=self._snapshot.message + payload.delta),
                generation=generation,
                stop_event=stop_event,
            )
            on_message = self.on_message
            if on_message is not None:
                self._dispatch(lambda: on_message(payload.delta), generation, stop_event)

    def _publish(
        self,
        snapshot: CodexSessionSnapshot,
        *,
        generation: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        if generation is not None and not self._is_current_generation(generation):
            return
        self._snapshot = snapshot
        on_state = self.on_state
        if on_state is not None:
            self._dispatch(lambda: on_state(snapshot), generation, stop_event)

    def _dispatch(
        self,
        callback: Callable[[], None],
        generation: int | None,
        stop_event: threading.Event | None = None,
    ) -> None:
        def guarded_callback() -> None:
            if generation is not None and not self._is_current_generation(generation):
                return
            if stop_event is not None and stop_event.is_set():
                return
            callback()

        self._callback_dispatcher(guarded_callback)

    def _is_current_generation(self, generation: int) -> bool:
        with self._state_lock:
            return generation == self._generation

    def _is_active(self, generation: int, stop_event: threading.Event) -> bool:
        return self._is_current_generation(generation) and not stop_event.is_set()

    @staticmethod
    def _default_client_factory(cwd: str | None = None) -> CodexClient:
        return create_codex_client(cwd=cwd)

    @staticmethod
    def _default_proposal_parser(payload: Mapping[str, object]) -> object:
        from .codex_edit_proposal import CodexEditProposal

        return CodexEditProposal.from_json(payload)
