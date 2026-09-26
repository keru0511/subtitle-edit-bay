from __future__ import annotations

from contextlib import nullcontext
import inspect
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, cast

from .codex_isolation import (
    CodexIsolationError,
    McpStatusClient,
    build_isolated_thread_params,
    build_isolated_turn_kwargs,
    collect_mcp_server_names,
    isolated_codex_cwd,
)
from .data_boundary import coerce_float, decode_json, is_object_list, is_object_mapping


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


class CodexClientProtocol(Protocol):
    """字幕提案の通常turnに必要なapp-server操作。"""

    def start(self) -> Mapping[str, object]: ...
    def stop(self) -> None: ...
    def account_read(self) -> Mapping[str, object]: ...
    def thread_start(self, params: Mapping[str, object] | None = None) -> Mapping[str, object]: ...
    def turn_start(
        self,
        *,
        thread_id: str,
        prompt: str,
        output_schema: Mapping[str, object] | None = None,
        context: Mapping[str, object] | None = None,
        approval_policy: str | None = None,
        sandbox_policy: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]: ...

    def turn_interrupt(self, turn_id: str, *, thread_id: str) -> Mapping[str, object]: ...


class _NoArgClientFactory(Protocol):
    def __call__(self) -> CodexClientProtocol: ...


class _CwdClientFactory(Protocol):
    def __call__(self, *, cwd: str) -> CodexClientProtocol: ...


ClientFactory = _NoArgClientFactory | _CwdClientFactory


class _StructuredTurnRunner(Protocol):
    def __call__(
        self,
        *,
        thread_id: str,
        prompt: str,
        output_schema: Mapping[str, object],
        context: Mapping[str, object],
        cwd: str,
        environments: list[Mapping[str, object]],
        approval_policy: str,
        runtime_workspace_roots: list[str | Path],
        sandbox_policy: Mapping[str, object],
        timeout: float,
    ) -> Mapping[str, object]: ...


class CodexSessionError(RuntimeError):
    pass


class _CodexSessionCancelled(Exception):
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
        self._client: CodexClientProtocol | None = None
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
                client.turn_interrupt(snapshot.turn_id, thread_id=snapshot.thread_id)
            except Exception:
                pass
        if client is not None:
            try:
                client.stop()
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
    ) -> object:
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
        client: CodexClientProtocol | None = None
        try:
            workspace_context = isolated_codex_cwd() if self.isolated_turn else nullcontext(None)
            with workspace_context as isolated_cwd:
                client = self._create_client(isolated_cwd)
                with self._state_lock:
                    if not self._is_active(generation, stop_event):
                        return
                    self._client = client
                self._attach_notification_callback(client, generation, stop_event)
                client.start()
                if not self._is_active(generation, stop_event):
                    return
                self._publish(
                    CodexSessionSnapshot(state="authenticating", revision=revision),
                    generation=generation,
                    stop_event=stop_event,
                )
                account = client.account_read()
                if not self._is_active(generation, stop_event):
                    return
                if not bool(account.get("authenticated", account.get("loggedIn", False))):
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
                thread_params: Mapping[str, object] = {
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                }
                if self.isolated_turn:
                    try:
                        if isolated_cwd is None:
                            raise CodexIsolationError("Codex turn working directory is unavailable")
                        mcp_client = self._mcp_status_client(client)
                        configured_mcp_names = collect_mcp_server_names(
                            mcp_client,
                            config_only=True,
                        )
                        thread_params = build_isolated_thread_params(
                            isolated_cwd,
                            mcp_server_names=configured_mcp_names,
                        )
                    except (AttributeError, CodexIsolationError) as error:
                        raise CodexSessionError("Codexの隔離設定を確認できません") from error
                thread = client.thread_start(thread_params)
                if not self._is_active(generation, stop_event):
                    return
                thread_payload = thread.get("thread", thread)
                thread_id = (str(thread_payload.get("id", "")) if isinstance(thread_payload, Mapping) else "") or str(
                    thread.get("threadId", "")
                )
                if not thread_id:
                    raise CodexSessionError("Codex thread id was not returned")
                if self.isolated_turn:
                    try:
                        exposed_mcp_names = collect_mcp_server_names(
                            self._mcp_status_client(client),
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
                response = self._run_turn(
                    client,
                    thread_id=thread_id,
                    prompt=prompt,
                    context=context,
                    output_schema=output_schema,
                    isolated_cwd=isolated_cwd,
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
        except _CodexSessionCancelled:
            return
        except Exception as error:
            if self._is_current_generation(generation) and not stop_event.is_set():
                self._publish(
                    CodexSessionSnapshot(
                        state="error",
                        thread_id=self._snapshot.thread_id,
                        turn_id=self._snapshot.turn_id,
                        revision=revision,
                        error=str(error),
                    ),
                    generation=generation,
                    stop_event=stop_event,
                )
        finally:
            if client is not None:
                try:
                    client.stop()
                except Exception:
                    pass
                with self._state_lock:
                    if self._client is client:
                        self._client = None

    def _create_client(self, cwd: str | None) -> CodexClientProtocol:
        factory = self.client_factory
        if cwd is None:
            return cast(_NoArgClientFactory, factory)()
        try:
            parameters: tuple[inspect.Parameter, ...] = tuple(inspect.signature(factory).parameters.values())
        except (TypeError, ValueError):
            parameters = ()
        accepts_cwd = any(
            parameter.name == "cwd" or parameter.kind is parameter.VAR_KEYWORD for parameter in parameters
        )
        if accepts_cwd:
            return cast(_CwdClientFactory, factory)(cwd=cwd)
        return cast(_NoArgClientFactory, factory)()

    @staticmethod
    def _mcp_status_client(client: CodexClientProtocol) -> McpStatusClient:
        if not hasattr(client, "mcp_server_status_list"):
            raise CodexIsolationError("Codex MCP inventory is unavailable")
        return cast(McpStatusClient, client)

    def _run_turn(
        self,
        client: CodexClientProtocol,
        *,
        thread_id: str,
        prompt: str,
        context: Mapping[str, object],
        output_schema: Mapping[str, object],
        isolated_cwd: str | None,
    ) -> Mapping[str, object]:
        if self.isolated_turn:
            runner_value = cast(object, getattr(client, "run_structured_turn", None))
            if not callable(runner_value) or isolated_cwd is None:
                raise CodexSessionError("Codexの構造化turn経路を利用できません")
            runner = cast(_StructuredTurnRunner, runner_value)
            return runner(
                thread_id=thread_id,
                prompt=prompt,
                output_schema=output_schema,
                context=context,
                **build_isolated_turn_kwargs(isolated_cwd),
                timeout=120.0,
            )
        return client.turn_start(
            thread_id=thread_id,
            prompt=prompt,
            output_schema=output_schema,
            context=context,
            approval_policy="never",
            sandbox_policy={
                "type": "readOnly",
                "networkAccess": False,
            },
        )

    def _attach_notification_callback(
        self,
        client: CodexClientProtocol,
        generation: int,
        stop_event: threading.Event,
    ) -> None:
        if hasattr(client, "notification_callback"):
            callback: Callable[[object], None] = lambda notification: self._on_notification(
                generation, stop_event, notification
            )
            setattr(
                client,
                "notification_callback",
                callback,
            )

    def _on_notification(
        self,
        generation: int,
        stop_event: threading.Event,
        notification: object,
    ) -> None:
        if not self._is_active(generation, stop_event):
            return
        method = str(cast(object, getattr(notification, "method", "")))
        params_value = cast(object, getattr(notification, "params", None))
        params: Mapping[object, object] = params_value if is_object_mapping(params_value) else {}
        notification_turn_id = str(params.get("turnId", ""))
        turn_payload = params.get("turn")
        if not notification_turn_id and is_object_mapping(turn_payload):
            notification_turn_id = str(turn_payload.get("id", ""))
        if "turn" in method.casefold() and notification_turn_id:
            self._publish(
                CodexSessionSnapshot(
                    state=self._snapshot.state,
                    thread_id=self._snapshot.thread_id,
                    turn_id=notification_turn_id,
                    revision=self._snapshot.revision,
                    message=self._snapshot.message,
                    proposal=self._snapshot.proposal,
                ),
                generation=generation,
                stop_event=stop_event,
            )
        delta = params.get("delta", params.get("text", ""))
        if delta:
            message = self._snapshot.message + str(delta)
            self._publish(
                CodexSessionSnapshot(
                    state=self._snapshot.state,
                    thread_id=self._snapshot.thread_id,
                    turn_id=self._snapshot.turn_id,
                    revision=self._snapshot.revision,
                    message=message,
                    proposal=self._snapshot.proposal,
                ),
                generation=generation,
            )
            on_message = self.on_message
            if on_message is not None:
                self._dispatch(lambda: on_message(str(delta)), generation, stop_event)

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
    def _default_client_factory(cwd: str | None = None) -> CodexClientProtocol:
        from .codex_app_server_client import CodexAppServerClient

        return CodexAppServerClient(cwd=cwd)

    @staticmethod
    def _default_proposal_parser(payload: Mapping[str, object]) -> object:
        from .codex_edit_proposal import CodexEditProposal

        return CodexEditProposal.from_json(payload)
