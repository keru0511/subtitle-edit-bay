from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol


CODEX_SCOPES = ("selected", "current", "time_range", "all")
CODEX_OUTPUT_SCHEMA: dict[str, Any] = {
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
    def start(self) -> Mapping[str, Any]: ...
    def stop(self) -> None: ...
    def account_read(self) -> Mapping[str, Any]: ...
    def thread_start(self, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...
    def thread_resume(self, thread_id: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...
    def turn_start(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def turn_interrupt(self, turn_id: str) -> Mapping[str, Any]: ...


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
    proposal: Mapping[str, Any] | None = None


def build_codex_context(
    project: Mapping[str, Any],
    scope: str,
    *,
    selected_segment_ids: set[str] | None = None,
    current_time: float | None = None,
    range_start: float | None = None,
    range_end: float | None = None,
) -> dict[str, Any]:
    """Build a safe, path-free context payload for a Codex turn."""
    if scope not in CODEX_SCOPES:
        raise ValueError(f"unknown Codex scope: {scope}")
    segments = [item for item in project.get("segments", []) if isinstance(item, Mapping)]
    if scope == "selected":
        selected = selected_segment_ids or set()
        segments = [item for item in segments if str(item.get("id")) in selected]
    elif scope == "current":
        if current_time is None:
            raise ValueError("current scope requires current_time")
        segments = [
            item for item in segments
            if float(item.get("start", 0.0)) <= current_time <= float(item.get("end", 0.0))
        ]
    elif scope == "time_range":
        if range_start is None or range_end is None or range_end < range_start:
            raise ValueError("time_range requires a valid range")
        segments = [
            item for item in segments
            if float(item.get("end", 0.0)) > range_start
            and float(item.get("start", 0.0)) < range_end
        ]
    safe_segments = [
        {field: item[field] for field in _CONTEXT_FIELDS if field in item}
        for item in segments
    ]
    return {
        "scope": scope,
        "segment_count": len(safe_segments),
        "segments": safe_segments,
        "subtitle_settings": {
            key: project.get("subtitle_settings", {}).get(key)
            for key in ("font_size", "outline_color", "outline_thickness")
            if key in project.get("subtitle_settings", {})
        },
    }


class CodexSessionController:
    def __init__(
        self,
        *,
        client_factory: Callable[[], CodexClientProtocol] | None = None,
        proposal_parser: Callable[[Mapping[str, Any]], Any] | None = None,
        on_state: Callable[[CodexSessionSnapshot], None] | None = None,
        on_message: Callable[[str], None] | None = None,
        on_proposal: Callable[[Mapping[str, Any]], None] | None = None,
        callback_dispatcher: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self.client_factory = client_factory or self._default_client_factory
        self.proposal_parser = proposal_parser or self._default_proposal_parser
        self.on_state = on_state
        self.on_message = on_message
        self.on_proposal = on_proposal
        self._callback_dispatcher = callback_dispatcher or (lambda callback: callback())
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
        context: Mapping[str, Any],
        output_schema: Mapping[str, Any] | None = None,
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
        self._thread = threading.Thread(
            target=self._run,
            kwargs={
                "prompt": prompt,
                "context": dict(context),
                "output_schema": dict(output_schema or CODEX_OUTPUT_SCHEMA),
                "revision": revision,
                "generation": generation,
                "stop_event": stop_event,
            },
            name="codex-edit-session",
            daemon=True,
        )
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
                client.turn_interrupt(snapshot.turn_id)
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
        project: Mapping[str, Any],
        proposal: Any,
        *,
        selected_operation_ids: set[str] | None = None,
        current_revision: int | None = None,
    ) -> Any:
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
        context: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        revision: int,
        generation: int,
        stop_event: threading.Event,
    ) -> None:
        client: CodexClientProtocol | None = None
        try:
            client = self.client_factory()
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
            thread = client.thread_start()
            if not self._is_active(generation, stop_event):
                return
            thread_id = str(thread.get("threadId", thread.get("id", "")))
            if not thread_id:
                raise CodexSessionError("Codex thread id was not returned")
            self._publish(
                CodexSessionSnapshot(state="running", thread_id=thread_id, revision=revision),
                generation=generation,
                stop_event=stop_event,
            )
            response = client.turn_start(
                thread_id=thread_id,
                prompt=prompt,
                output_schema=output_schema,
                context=context,
            )
            if not self._is_active(generation, stop_event):
                return
            raw_proposal = response.get("proposal", response.get("output", response))
            if isinstance(raw_proposal, str):
                raw_proposal = json.loads(raw_proposal)
            if not isinstance(raw_proposal, Mapping):
                raise CodexSessionError("Codex output is not a proposal object")
            self.proposal_parser(raw_proposal)
            proposal = dict(raw_proposal)
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
            if self.on_proposal is not None:
                self._dispatch(
                    lambda: self.on_proposal(proposal),
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

    def _attach_notification_callback(
        self,
        client: CodexClientProtocol,
        generation: int,
        stop_event: threading.Event,
    ) -> None:
        if hasattr(client, "notification_callback"):
            setattr(
                client,
                "notification_callback",
                lambda notification: self._on_notification(
                    generation, stop_event, notification
                ),
            )

    def _on_notification(
        self,
        generation: int,
        stop_event: threading.Event,
        notification: Any,
    ) -> None:
        if not self._is_active(generation, stop_event):
            return
        method = str(getattr(notification, "method", ""))
        params = getattr(notification, "params", {})
        if not isinstance(params, Mapping):
            params = {}
        if "turn" in method.casefold() and params.get("turnId"):
            self._publish(
                CodexSessionSnapshot(
                    state=self._snapshot.state,
                    thread_id=self._snapshot.thread_id,
                    turn_id=str(params["turnId"]),
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
            if self.on_message is not None:
                self._dispatch(lambda: self.on_message(str(delta)), generation, stop_event)

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
        if self.on_state is not None:
            self._dispatch(lambda: self.on_state(snapshot), generation, stop_event)

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
    def _default_client_factory() -> CodexClientProtocol:
        from .codex_app_server_client import CodexAppServerClient

        return CodexAppServerClient()

    @staticmethod
    def _default_proposal_parser(payload: Mapping[str, Any]) -> Any:
        from .codex_edit_proposal import CodexEditProposal

        return CodexEditProposal.from_json(payload)

