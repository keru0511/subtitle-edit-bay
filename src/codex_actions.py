from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import math
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Protocol

from .codex_review import (
    ReviewError,
    StaleReviewError,
    build_review_context,
    review_context,
    review_context_with_codex,
)

ACTION_SCHEMA_VERSION = 1
CANCELLABLE_JOB_TYPES = frozenset(
    {"transcribe", "highlight_analysis", "render", "render_short"}
)


class ActionKind(str, Enum):
    INSPECT = "inspect"
    PROPOSE = "propose"
    EXECUTE = "execute"


class ActionStatus(str, Enum):
    SUCCESS = "success"
    REJECTED = "rejected"
    FAILED = "failed"


class ActionErrorCode(str, Enum):
    INVALID_SCHEMA = "invalid_schema"
    UNKNOWN_ACTION = "unknown_action"
    KIND_MISMATCH = "kind_mismatch"
    OUT_OF_SCOPE = "out_of_scope"
    STALE_REVISION = "stale_revision"
    CONFIRMATION_REQUIRED = "confirmation_required"
    PRECONDITION_FAILED = "precondition_failed"
    JOB_CONFLICT = "job_conflict"
    INVALID_OPERATION = "invalid_operation"
    HANDLER_FAILED = "handler_failed"


class RevisionPolicy(str, Enum):
    NONE = "none"
    CURRENT = "current"


class ConfirmationPolicy(str, Enum):
    NONE = "none"
    EXPLICIT_APPLY = "explicit_apply"
    WHEN_DESTRUCTIVE = "when_destructive"


class ActionRejected(ValueError):
    def __init__(self, code: ActionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ActionRequest:
    kind: ActionKind
    type: str
    args: Mapping[str, Any]
    scope_id: str
    project_revision: int | None = None
    schema_version: int = ACTION_SCHEMA_VERSION

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ActionRequest":
        if not isinstance(payload, Mapping):
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "action must be an object")
        allowed = {"schema_version", "kind", "type", "args", "scope_id", "project_revision"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ActionRejected(
                ActionErrorCode.INVALID_SCHEMA,
                "action contains unsupported fields: " + ", ".join(unknown),
            )
        if payload.get("schema_version") != ACTION_SCHEMA_VERSION:
            raise ActionRejected(
                ActionErrorCode.INVALID_SCHEMA,
                f"schema_version must be {ACTION_SCHEMA_VERSION}",
            )
        try:
            kind = ActionKind(payload.get("kind"))
        except ValueError as error:
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "kind must be inspect, propose, or execute") from error
        action_type = payload.get("type")
        if not isinstance(action_type, str) or not action_type.strip():
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "type must be a non-empty string")
        args = payload.get("args")
        if not isinstance(args, Mapping):
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "args must be an object")
        scope_id = payload.get("scope_id")
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "scope_id must be a non-empty string")
        revision = payload.get("project_revision")
        if revision is not None and (type(revision) is not int or revision < 0):
            raise ActionRejected(
                ActionErrorCode.INVALID_SCHEMA,
                "project_revision must be a non-negative integer",
            )
        return cls(
            kind=kind,
            type=action_type,
            args=deepcopy(dict(args)),
            scope_id=scope_id,
            project_revision=revision,
        )


@dataclass(frozen=True)
class ActionScope:
    """Backend-owned authorization derived from one explicit user request."""

    id: str
    allowed_actions: frozenset[str]
    project_revision: int | None = None
    confirmed_actions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("scope id must not be empty")


@dataclass(frozen=True)
class FieldSchema:
    types: tuple[type, ...]
    required: bool = False
    choices: frozenset[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    non_empty: bool = False


DestructivePredicate = Callable[[Mapping[str, Any]], bool]


@dataclass(frozen=True)
class ActionDefinition:
    kind: ActionKind
    fields: Mapping[str, FieldSchema] = field(default_factory=dict)
    revision_policy: RevisionPolicy = RevisionPolicy.NONE
    confirmation_policy: ConfirmationPolicy = ConfirmationPolicy.NONE
    conflicts_with_job: bool = False
    destructive_when: DestructivePredicate = lambda _args: False


@dataclass(frozen=True)
class HandlerResult:
    message: str
    state: Mapping[str, Any] | None = None
    proposal: Mapping[str, Any] | None = None
    job: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ActionResult:
    status: ActionStatus
    action_type: str = ""
    code: str = ""
    message: str = ""
    revision: int | None = None
    current_state: Mapping[str, Any] | None = None
    proposal: Mapping[str, Any] | None = None
    job: Mapping[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status.value,
            "action_type": self.action_type,
            "code": self.code,
            "message": self.message,
        }
        if self.revision is not None:
            payload["revision"] = self.revision
        if self.current_state is not None:
            payload["current_state"] = deepcopy(dict(self.current_state))
        if self.proposal is not None:
            payload["proposal"] = deepcopy(dict(self.proposal))
        if self.job is not None:
            payload["job"] = deepcopy(dict(self.job))
        return payload


class ActionBackend(Protocol):
    @property
    def current_revision(self) -> int: ...

    @property
    def active_job(self) -> str: ...

    def inspect(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult: ...

    def propose(self, action_type: str, args: Mapping[str, Any], revision: int) -> HandlerResult: ...

    def execute(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult: ...


ACTION_DEFINITIONS: Mapping[str, ActionDefinition] = {
    "inspect_project_state": ActionDefinition(ActionKind.INSPECT),
    "inspect_subtitle_state": ActionDefinition(ActionKind.INSPECT),
    "inspect_audio_mix_state": ActionDefinition(ActionKind.INSPECT),
    "inspect_timeline_state": ActionDefinition(ActionKind.INSPECT),
    "inspect_processing_state": ActionDefinition(ActionKind.INSPECT),
    "inspect_dependency_state": ActionDefinition(ActionKind.INSPECT),
    "inspect_render_state": ActionDefinition(ActionKind.INSPECT),
    "inspect_selection_state": ActionDefinition(ActionKind.INSPECT),
    "review_project": ActionDefinition(
        ActionKind.INSPECT,
        fields={"subtitle_chunk_size": FieldSchema((int,), minimum=25, maximum=500)},
    ),
    "propose_subtitle_edit": ActionDefinition(
        ActionKind.PROPOSE,
        fields={
            "intent": FieldSchema((str,), required=True, non_empty=True),
            "selection_scope": FieldSchema(
                (str,),
                required=True,
                choices=frozenset({"selected", "current", "time_range", "all"}),
            ),
            "range_start": FieldSchema((int, float), minimum=0.0),
            "range_end": FieldSchema((int, float), minimum=0.0),
        },
        revision_policy=RevisionPolicy.CURRENT,
        confirmation_policy=ConfirmationPolicy.EXPLICIT_APPLY,
        conflicts_with_job=True,
    ),
    "propose_audio_mix": ActionDefinition(
        ActionKind.PROPOSE,
        fields={"intent": FieldSchema((str,), required=True, non_empty=True)},
        revision_policy=RevisionPolicy.CURRENT,
        confirmation_policy=ConfirmationPolicy.EXPLICIT_APPLY,
        conflicts_with_job=True,
    ),
    "propose_timeline_edit": ActionDefinition(
        ActionKind.PROPOSE,
        fields={
            "intent": FieldSchema((str,), required=True, non_empty=True),
            "target": FieldSchema(
                (str,),
                required=True,
                choices=frozenset({"normal", "short"}),
            ),
        },
        revision_policy=RevisionPolicy.CURRENT,
        confirmation_policy=ConfirmationPolicy.EXPLICIT_APPLY,
        conflicts_with_job=True,
    ),
    "start_transcription": ActionDefinition(
        ActionKind.EXECUTE,
        fields={
            "mode": FieldSchema((str,), required=True, choices=frozenset({"merge", "replace"})),
        },
        revision_policy=RevisionPolicy.CURRENT,
        confirmation_policy=ConfirmationPolicy.WHEN_DESTRUCTIVE,
        conflicts_with_job=True,
        destructive_when=lambda args: args.get("mode") == "replace",
    ),
    "start_highlight_analysis": ActionDefinition(
        ActionKind.EXECUTE,
        revision_policy=RevisionPolicy.CURRENT,
        conflicts_with_job=True,
    ),
    "rebuild_subtitle_preview": ActionDefinition(
        ActionKind.EXECUTE,
        revision_policy=RevisionPolicy.CURRENT,
        conflicts_with_job=True,
    ),
    "render_normal": ActionDefinition(
        ActionKind.EXECUTE,
        fields={"overwrite": FieldSchema((bool,))},
        revision_policy=RevisionPolicy.CURRENT,
        confirmation_policy=ConfirmationPolicy.WHEN_DESTRUCTIVE,
        conflicts_with_job=True,
        destructive_when=lambda args: bool(args.get("overwrite", False)),
    ),
    "render_short": ActionDefinition(
        ActionKind.EXECUTE,
        fields={"overwrite": FieldSchema((bool,))},
        revision_policy=RevisionPolicy.CURRENT,
        confirmation_policy=ConfirmationPolicy.WHEN_DESTRUCTIVE,
        conflicts_with_job=True,
        destructive_when=lambda args: bool(args.get("overwrite", False)),
    ),
    "cancel_processing": ActionDefinition(
        ActionKind.EXECUTE,
        fields={
            "job_id": FieldSchema((str,), required=True, non_empty=True),
            "job_type": FieldSchema(
                (str,),
                choices=CANCELLABLE_JOB_TYPES,
            ),
        },
    ),
}


class ActionDispatcher:
    """Validate an untrusted envelope before dispatching an allowlisted backend call."""

    def __init__(
        self,
        backend: ActionBackend,
        definitions: Mapping[str, ActionDefinition] = ACTION_DEFINITIONS,
    ) -> None:
        self._backend = backend
        self._definitions = dict(definitions)

    @property
    def allowed_action_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def dispatch(self, payload: Mapping[str, Any], *, trusted_scope: ActionScope) -> ActionResult:
        action_type = str(payload.get("type", "")) if isinstance(payload, Mapping) else ""
        try:
            request = ActionRequest.from_json(payload)
            action_type = request.type
            definition = self._definitions.get(request.type)
            if definition is None:
                raise ActionRejected(ActionErrorCode.UNKNOWN_ACTION, f"unknown action type: {request.type}")
            if request.kind is not definition.kind:
                raise ActionRejected(
                    ActionErrorCode.KIND_MISMATCH,
                    f"{request.type} must use kind {definition.kind.value}",
                )
            self._validate_scope(request, trusted_scope)
            self._validate_args(request.args, definition.fields)
            self._validate_revision(request, trusted_scope, definition)
            if definition.conflicts_with_job and self._backend.active_job:
                raise ActionRejected(
                    ActionErrorCode.JOB_CONFLICT,
                    f"action conflicts with running job: {self._backend.active_job}",
                )
            if (
                definition.confirmation_policy is ConfirmationPolicy.WHEN_DESTRUCTIVE
                and definition.destructive_when(request.args)
                and request.type not in trusted_scope.confirmed_actions
            ):
                raise ActionRejected(
                    ActionErrorCode.CONFIRMATION_REQUIRED,
                    "destructive action requires backend-trusted confirmation",
                )
            handler_result = self._call_handler(request)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                action_type=request.type,
                message=handler_result.message,
                revision=self._backend.current_revision,
                current_state=handler_result.state,
                proposal=handler_result.proposal,
                job=handler_result.job,
            )
        except ActionRejected as error:
            return ActionResult(
                status=ActionStatus.REJECTED,
                action_type=action_type,
                code=error.code.value,
                message=str(error),
                revision=self._backend.current_revision,
            )
        except Exception:
            # Do not expose exception details, paths, or backend internals to Codex.
            return ActionResult(
                status=ActionStatus.FAILED,
                action_type=action_type,
                code=ActionErrorCode.HANDLER_FAILED.value,
                message="action handler failed",
                revision=self._backend.current_revision,
            )

    @staticmethod
    def _validate_scope(request: ActionRequest, scope: ActionScope) -> None:
        if request.scope_id != scope.id or request.type not in scope.allowed_actions:
            raise ActionRejected(ActionErrorCode.OUT_OF_SCOPE, "action is outside the trusted user request scope")

    def _validate_revision(
        self,
        request: ActionRequest,
        scope: ActionScope,
        definition: ActionDefinition,
    ) -> None:
        if definition.revision_policy is RevisionPolicy.NONE:
            return
        if request.project_revision is None:
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "project_revision is required")
        if scope.project_revision is None:
            raise ActionRejected(
                ActionErrorCode.STALE_REVISION,
                "trusted action scope is not bound to a project revision",
            )
        current = self._backend.current_revision
        if request.project_revision != current or scope.project_revision != current:
            raise ActionRejected(ActionErrorCode.STALE_REVISION, "project revision is stale")

    @staticmethod
    def _validate_args(args: Mapping[str, Any], fields: Mapping[str, FieldSchema]) -> None:
        unknown = sorted(set(args) - set(fields))
        if unknown:
            raise ActionRejected(
                ActionErrorCode.INVALID_SCHEMA,
                "args contains unsupported fields: " + ", ".join(unknown),
            )
        for name, schema in fields.items():
            if name not in args:
                if schema.required:
                    raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, f"args.{name} is required")
                continue
            value = args[name]
            if bool in schema.types:
                valid_type = type(value) is bool
            elif any(item in (int, float) for item in schema.types):
                valid_type = type(value) in schema.types and not isinstance(value, bool)
            else:
                valid_type = isinstance(value, schema.types)
            if not valid_type:
                raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, f"args.{name} has an invalid type")
            if schema.non_empty and isinstance(value, str) and not value.strip():
                raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, f"args.{name} must not be empty")
            if schema.choices is not None and value not in schema.choices:
                raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, f"args.{name} has an unsupported value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                number = float(value)
                if not math.isfinite(number):
                    raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, f"args.{name} must be finite")
                if schema.minimum is not None and number < schema.minimum:
                    raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, f"args.{name} is below the minimum")
                if schema.maximum is not None and number > schema.maximum:
                    raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, f"args.{name} is above the maximum")
        if args.get("selection_scope") == "time_range":
            start = args.get("range_start")
            end = args.get("range_end")
            if start is None or end is None or float(end) <= float(start):
                raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "time_range requires an increasing range")

    def _call_handler(self, request: ActionRequest) -> HandlerResult:
        if request.kind is ActionKind.INSPECT:
            return self._backend.inspect(request.type, request.args)
        if request.kind is ActionKind.PROPOSE:
            assert request.project_revision is not None
            return self._backend.propose(request.type, request.args, request.project_revision)
        return self._backend.execute(request.type, request.args)


class GuiActionBackend:
    """Fixed adapter to existing GUI methods; no reflection or arbitrary method names."""

    def __init__(self, backend: Any) -> None:
        self._gui = backend
        self._inspect_handlers: Mapping[str, Callable[[Mapping[str, Any]], HandlerResult]] = {
            "inspect_project_state": self._inspect_project,
            "inspect_subtitle_state": self._inspect_subtitles,
            "inspect_audio_mix_state": self._inspect_audio,
            "inspect_timeline_state": self._inspect_timeline,
            "inspect_processing_state": self._inspect_processing,
            "inspect_dependency_state": self._inspect_dependencies,
            "inspect_render_state": self._inspect_render,
            "inspect_selection_state": self._inspect_selection,
            "review_project": self._review_project,
        }
        self._propose_handlers: Mapping[str, Callable[[Mapping[str, Any], int], HandlerResult]] = {
            "propose_subtitle_edit": self._propose_subtitle,
            "propose_audio_mix": self._propose_audio,
            "propose_timeline_edit": self._propose_timeline,
        }
        self._execute_handlers: Mapping[str, Callable[[Mapping[str, Any]], HandlerResult]] = {
            "start_transcription": self._start_transcription,
            "start_highlight_analysis": self._start_highlight,
            "rebuild_subtitle_preview": self._rebuild_subtitle_preview,
            "render_normal": self._render_normal,
            "render_short": self._render_short,
            "cancel_processing": self._cancel_processing,
        }

    @property
    def current_revision(self) -> int:
        return int(self._gui._project_revision)

    @property
    def active_job(self) -> str:
        registered_process_job = self._registered_process_job()
        if registered_process_job:
            return registered_process_job
        if bool(self._gui._running):
            return str(self._gui._active_job or "processing")
        for attribute, job_name in (
            ("_codex_session", "subtitle_proposal"),
            ("_codex_audio_mix_session", "audio_mix_proposal"),
            ("_codex_timeline_session", "timeline_proposal"),
        ):
            session = getattr(self._gui, attribute, None)
            if session is not None and bool(getattr(session, "running", False)):
                return job_name
        if str(self._gui.highlightAnalysisState) in {"running", "cancelling"}:
            return "highlight_analysis"
        return ""

    def inspect(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult:
        handler = self._inspect_handlers.get(action_type)
        if handler is None:
            raise ActionRejected(ActionErrorCode.UNKNOWN_ACTION, "inspect action has no backend handler")
        return handler(args)

    def propose(self, action_type: str, args: Mapping[str, Any], revision: int) -> HandlerResult:
        handler = self._propose_handlers.get(action_type)
        if handler is None:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "proposal domain is not available")
        return handler(args, revision)

    def execute(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult:
        handler = self._execute_handlers.get(action_type)
        if handler is None:
            raise ActionRejected(ActionErrorCode.UNKNOWN_ACTION, "execute action has no backend handler")
        return handler(args)

    def _inspect_project(self, _args: Mapping[str, Any]) -> HandlerResult:
        project = self._gui._project
        state = {
            "loaded": project is not None,
            "dirty": bool(self._gui._project_dirty),
            "revision": self.current_revision,
            "segment_count": len(project.get("segments", [])) if project else 0,
            "has_video": bool(project and project.get("video")),
        }
        return HandlerResult("project state inspected", state=state)

    def _inspect_subtitles(self, _args: Mapping[str, Any]) -> HandlerResult:
        segments = self._gui.subtitleSegments
        safe_fields = {"id", "start", "end", "text", "speaker", "emphasis", "position"}
        safe = [{key: value for key, value in item.items() if key in safe_fields} for item in segments]
        return HandlerResult("subtitle state inspected", state={"segments": safe})

    def _inspect_audio(self, _args: Mapping[str, Any]) -> HandlerResult:
        safe_fields = {"id", "label", "kind", "enabled", "volume_percent", "delay_seconds"}
        channels = [
            {key: value for key, value in item.items() if key in safe_fields} for item in self._gui.audioMixerChannels
        ]
        return HandlerResult("audio mix state inspected", state={"channels": channels})

    def _inspect_timeline(self, _args: Mapping[str, Any]) -> HandlerResult:
        return HandlerResult("timeline state inspected", state=deepcopy(dict(self._gui.cutTimeline)))

    def _inspect_processing(self, _args: Mapping[str, Any]) -> HandlerResult:
        active_job = self.active_job
        registered_process_job = self._registered_process_job()
        if active_job == "highlight_analysis":
            state = {
                "job_id": self._job_id_for(active_job),
                "job_type": "highlight_analysis",
                "active_job": active_job,
                "running": True,
                "progress": float(self._gui.highlightAnalysisProgress),
                "progress_percent": int(round(float(self._gui.highlightAnalysisProgress) * 100)),
                "status": str(self._gui.highlightAnalysisState),
                "current_detail": "見どころを解析中",
                "steps": [],
                "can_cancel": str(self._gui.highlightAnalysisState) == "running",
            }
        elif active_job == "subtitle_proposal":
            snapshot = self._gui._codex_session.snapshot
            state = {
                "active_job": active_job,
                "running": bool(self._gui._codex_session.running),
                "progress": None,
                "progress_known": False,
                "status": str(snapshot.state),
                "steps": [],
            }
        elif (
            str(self._gui.highlightAnalysisState) in {"completed", "cancelled", "error"}
            and not str(self._gui._processing_progress.job)
        ):
            status = str(self._gui.highlightAnalysisState)
            state = {
                "job_id": self._job_id_for("highlight_analysis"),
                "job_type": "highlight_analysis",
                "active_job": "",
                "running": False,
                "progress": float(self._gui.highlightAnalysisProgress),
                "progress_percent": int(round(float(self._gui.highlightAnalysisProgress) * 100)),
                "status": status,
                "current_detail": "",
                "steps": [],
                "can_cancel": False,
                "terminal_result": status,
            }
        else:
            tracker = self._gui._processing_progress
            status = str(tracker.status)
            current_detail = next(
                (
                    str(step.get("label", ""))
                    for step in tracker.as_list()
                    if step.get("id") == tracker.current_step
                ),
                "",
            )
            state = {
                "job_id": str(tracker.job_id),
                "job_type": str(self._gui._active_job or tracker.job),
                "active_job": active_job,
                "running": bool(self._gui._running) or bool(registered_process_job),
                "progress": float(tracker.value),
                "progress_percent": int(round(float(tracker.value) * 100)),
                "status": status,
                "current_detail": current_detail,
                "steps": tracker.as_list(),
                "can_cancel": bool(self._gui._running)
                and str(self._gui._active_job) != "update",
            }
            if status in {"completed", "cancelled", "error"}:
                state["terminal_result"] = status
            terminal_results = []
            if status in {"completed", "cancelled", "error"} and tracker.job:
                terminal_results.append(
                    {
                        "job_id": str(tracker.job_id),
                        "type": str(tracker.job),
                        "status": status,
                    }
                )
            highlight_status = str(self._gui.highlightAnalysisState)
            if highlight_status in {"completed", "cancelled", "error"}:
                terminal_results.append(
                    {
                        "job_id": self._job_id_for("highlight_analysis"),
                        "type": "highlight_analysis",
                        "status": highlight_status,
                    }
                )
            if terminal_results:
                state["terminal_results"] = terminal_results
        return HandlerResult("processing state inspected", state=state)

    def _inspect_dependencies(self, _args: Mapping[str, Any]) -> HandlerResult:
        dependencies = self._gui._dependencies
        state = {
            "ready": bool(dependencies.ready),
            "ffmpeg": bool(dependencies.ffmpeg),
            "ffprobe": bool(dependencies.ffprobe),
            "whisperx": bool(dependencies.whisperx),
            "cuda": bool(dependencies.cuda),
            "nvenc": bool(dependencies.nvenc),
        }
        return HandlerResult("dependency state inspected", state=state)

    def _inspect_render(self, _args: Mapping[str, Any]) -> HandlerResult:
        return HandlerResult("render state inspected", state=deepcopy(dict(self._gui.actionCapabilities)))

    def _inspect_selection(self, _args: Mapping[str, Any]) -> HandlerResult:
        segment_id = ""
        index = int(self._gui._selected_segment_index)
        segments = self._gui._project.get("segments", []) if self._gui._project else []
        if 0 <= index < len(segments):
            segment_id = str(segments[index].get("id", ""))
        return HandlerResult(
            "selection state inspected",
            state={"segment_id": segment_id, "playhead": deepcopy(dict(self._gui.editorPlayhead))},
        )

    def _review_project(self, args: Mapping[str, Any]) -> HandlerResult:
        """Review the current project through the isolated structured-turn contract."""

        try:
            context = build_review_context(
                self._gui,
                subtitle_chunk_size=int(args.get("subtitle_chunk_size", 200)),
                route_availability=self._review_route_availability(),
            )
        except StaleReviewError as error:
            raise ActionRejected(
                ActionErrorCode.STALE_REVISION,
                "project changed while review was starting",
            ) from error
        except ReviewError as error:
            raise ActionRejected(
                ActionErrorCode.PRECONDITION_FAILED,
                "a safe review context could not be created",
            ) from error

        if not context["project"]["loaded"]:
            result = review_context(context)
            return HandlerResult(
                "project review completed",
                state={"review_result": result.to_json(), "reviewed_chunks": 0},
            )

        client_factory = getattr(self._gui, "_create_codex_chat_client", None)
        if not callable(client_factory):
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "Codex review is unavailable")

        review_workspace = TemporaryDirectory(prefix="subtitle-edit-bay-review-")
        client = None
        try:
            # The review client must be created in a fresh workspace so the
            # review turn cannot inherit the normal chat's tools or state.
            client = client_factory(cwd=review_workspace.name)
            client.start()
            if not self._codex_account_authenticated(client.account_read(refresh_token=False)):
                raise ActionRejected(
                    ActionErrorCode.PRECONDITION_FAILED,
                    "Codex login is required before project review",
                )
            codex_chat = getattr(self._gui, "_codex_chat", None)
            snapshot = getattr(codex_chat, "snapshot", None)
            model = str(getattr(snapshot, "selected_model", "") or "")
            result = review_context_with_codex(
                context,
                client=client,
                isolated_cwd=review_workspace.name,
                model=model,
                current_revision=lambda: self.current_revision,
            )
        except ActionRejected:
            raise
        except StaleReviewError as error:
            raise ActionRejected(
                ActionErrorCode.STALE_REVISION,
                "project changed while review was running",
            ) from error
        except Exception as error:
            raise ActionRejected(
                ActionErrorCode.PRECONDITION_FAILED,
                "Codex could not complete the project review",
            ) from error
        finally:
            if client is not None:
                try:
                    client.stop()
                except Exception:
                    pass
            review_workspace.cleanup()

        return HandlerResult(
            "project review completed",
            state={
                "review_result": result.to_json(),
                "reviewed_chunks": max(1, len(context["subtitle_chunks"])),
            },
        )

    def _review_route_availability(self) -> dict[str, bool]:
        return {
            "subtitle_proposal": "propose_subtitle_edit" in self._propose_handlers,
            "audio_mix_proposal": "propose_audio_mix" in self._propose_handlers,
            "timeline_proposal": (
                "propose_timeline_edit" in self._propose_handlers
                and bool(getattr(self._gui, "_cut_editor_available", True))
            ),
            "processing_action": bool(self._execute_handlers),
        }

    @staticmethod
    def _codex_account_authenticated(account: Mapping[str, Any]) -> bool:
        if isinstance(account.get("account"), Mapping):
            return True
        legacy = account.get("authenticated", account.get("loggedIn"))
        if legacy is not None:
            return bool(legacy)
        return account.get("requiresOpenaiAuth") is False

    def _propose_subtitle(self, args: Mapping[str, Any], _revision: int) -> HandlerResult:
        if bool(self._gui._codex_session.running):
            raise ActionRejected(ActionErrorCode.JOB_CONFLICT, "a subtitle proposal is already being generated")
        self._gui.startCodexEdit(
            str(args["intent"]),
            str(args["selection_scope"]),
            float(args.get("range_start", 0.0)),
            float(args.get("range_end", 0.0)),
        )
        if not self._gui._codex_session.running:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "subtitle proposal could not be started")
        return HandlerResult("subtitle proposal generation started", state={"status": "running"})

    def _propose_audio(self, args: Mapping[str, Any], revision: int) -> HandlerResult:
        if not self._gui.start_codex_audio_mix_proposal(
            intent=str(args["intent"]),
            revision=revision,
        ):
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "audio mix proposal could not be started")
        return HandlerResult("audio mix proposal generation started", state={"status": "running"})

    def _propose_timeline(self, args: Mapping[str, Any], _revision: int) -> HandlerResult:
        if not self._gui.start_codex_timeline_proposal(
            intent=str(args["intent"]),
            target=str(args["target"]),
        ):
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "timeline proposal could not be started")
        return HandlerResult(
            "timeline proposal generation started",
            state={"status": "running", "target": str(args["target"])},
        )

    def _start_transcription(self, args: Mapping[str, Any]) -> HandlerResult:
        capabilities = self._gui.actionCapabilities
        if not capabilities.get("canTranscribe"):
            raise ActionRejected(
                ActionErrorCode.PRECONDITION_FAILED,
                str(capabilities.get("transcriptionReason") or "transcription is unavailable"),
            )
        self._gui.transcribeProject(dict(self._gui.settings), str(args["mode"]))
        self._require_started_job("transcribe")
        return HandlerResult("transcription started", job=self._current_job())

    def _start_highlight(self, _args: Mapping[str, Any]) -> HandlerResult:
        if not self._gui.startHighlightAnalysis():
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "highlight analysis is unavailable")
        return HandlerResult("highlight analysis started", job=self._current_job())

    def _rebuild_subtitle_preview(self, _args: Mapping[str, Any]) -> HandlerResult:
        if self._gui._project is None:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "project is not loaded")
        self._gui.buildSubtitlePreview(dict(self._gui.settings))
        if not str(self._gui.assPath) or str(getattr(self._gui, "stage", "")) == "ERROR":
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "subtitle preview could not be rebuilt")
        return HandlerResult(
            "subtitle preview rebuilt",
            job={
                "type": "subtitle_preview",
                "status": "completed",
                "progress_percent": 100,
                "terminal_result": "completed",
            },
        )

    def _render_normal(self, args: Mapping[str, Any]) -> HandlerResult:
        self._require_render("normal", args)
        self._gui.renderVideo(dict(self._gui.settings))
        self._require_started_job("render")
        return HandlerResult("normal render started", job=self._current_job())

    def _render_short(self, args: Mapping[str, Any]) -> HandlerResult:
        self._require_render("short", args)
        self._gui.renderShortVideo()
        self._require_started_job("render_short")
        return HandlerResult("short render started", job=self._current_job())

    def _cancel_processing(self, args: Mapping[str, Any]) -> HandlerResult:
        active_job = self.active_job
        expected_job_id = str(args.get("job_id", ""))
        expected_job_type = str(args.get("job_type", ""))
        if not expected_job_id:
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "job_id is required")
        if not active_job:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "no cancellable job is running")
        if active_job not in CANCELLABLE_JOB_TYPES:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "no cancellable job is running")
        current_job_id = self._job_id_for(active_job)
        if (
            not current_job_id
            or expected_job_id != current_job_id
            or (expected_job_type and expected_job_type != active_job)
        ):
            raise ActionRejected(ActionErrorCode.STALE_REVISION, "the selected job is no longer running")
        if active_job == "highlight_analysis":
            if not self._gui.cancelHighlightAnalysis():
                raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "highlight analysis could not be cancelled")
        else:
            if not bool(self._gui._running):
                raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "processing job is not cancellable yet")
            self._gui.cancelProcessing()
        return HandlerResult(
            "processing cancellation requested",
            job={
                "job_id": current_job_id,
                "type": active_job,
                "status": "cancelling",
                "progress_percent": self._progress_percent(),
            },
        )

    def _progress_percent(self) -> int:
        if self.active_job == "highlight_analysis":
            return int(round(float(self._gui.highlightAnalysisProgress) * 100))
        return int(round(float(self._gui._processing_progress.value) * 100))

    def _current_job(self) -> dict[str, Any]:
        job_type = self.active_job
        job_id = self._job_id_for(job_type)
        if not job_id:
            raise ActionRejected(
                ActionErrorCode.PRECONDITION_FAILED,
                "started job has no tracking identity",
            )
        return {
            "job_id": job_id,
            "type": job_type,
            "status": "running",
            "progress_percent": self._progress_percent(),
            "inspect_action": "inspect_processing_state",
            "cancel_action": "cancel_processing",
        }

    def _job_id_for(self, job_type: str) -> str:
        if job_type == "highlight_analysis":
            return str(getattr(self._gui, "_highlight_job_id", ""))
        tracker = getattr(self._gui, "_processing_progress", None)
        if tracker is None or str(getattr(tracker, "job", "")) != job_type:
            return ""
        return str(getattr(tracker, "job_id", ""))

    def _registered_process_job(self) -> str:
        job_type = str(getattr(self._gui, "_active_job", ""))
        tracker = getattr(self._gui, "_processing_progress", None)
        if (
            not job_type
            or tracker is None
            or str(getattr(tracker, "job", "")) != job_type
            or not str(getattr(tracker, "job_id", ""))
            or str(getattr(tracker, "status", "")) != "running"
        ):
            return ""
        return job_type

    def _require_render(self, kind: str, args: Mapping[str, Any]) -> None:
        capabilities = self._gui.actionCapabilities
        prefix = "normal" if kind == "normal" else "short"
        if capabilities.get(f"{prefix}RenderNeedsOutput"):
            raise ActionRejected(
                ActionErrorCode.PRECONDITION_FAILED,
                "render output must be selected in the GUI first",
            )
        enabled_key = "canRenderNormal" if kind == "normal" else "canRenderShort"
        reason_key = "normalRenderReason" if kind == "normal" else "shortRenderReason"
        if not capabilities.get(enabled_key):
            raise ActionRejected(
                ActionErrorCode.PRECONDITION_FAILED,
                str(capabilities.get(reason_key) or "render is unavailable"),
            )
        if self._gui.codex_render_output_exists(short=kind == "short") and not bool(args.get("overwrite", False)):
            raise ActionRejected(
                ActionErrorCode.CONFIRMATION_REQUIRED,
                "existing render output requires confirmed overwrite scope",
            )

    def _require_started_job(self, expected: str) -> None:
        if self._registered_process_job() != expected:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, f"{expected} job could not be started")


def build_gui_action_dispatcher(backend: Any) -> ActionDispatcher:
    return ActionDispatcher(GuiActionBackend(backend))
