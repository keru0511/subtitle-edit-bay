from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Callable, Mapping, Protocol

from .audio_mix_proposal import build_audio_mix_context

ACTION_SCHEMA_VERSION = 1


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
        fields={"intent": FieldSchema((str,), required=True, non_empty=True)},
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
        current = self._backend.current_revision
        if request.project_revision != current or (
            scope.project_revision is not None and scope.project_revision != current
        ):
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
        }
        self._propose_handlers: Mapping[str, Callable[[Mapping[str, Any], int], HandlerResult]] = {
            "propose_subtitle_edit": self._propose_subtitle,
            "propose_audio_mix": self._propose_audio,
        }
        self._execute_handlers: Mapping[str, Callable[[Mapping[str, Any]], HandlerResult]] = {
            "start_transcription": self._start_transcription,
            "start_highlight_analysis": self._start_highlight,
            "render_normal": self._render_normal,
            "render_short": self._render_short,
        }

    @property
    def current_revision(self) -> int:
        return int(self._gui._project_revision)

    @property
    def active_job(self) -> str:
        if bool(self._gui._running):
            return str(self._gui._active_job or "processing")
        subtitle_session = getattr(self._gui, "_codex_session", None)
        if subtitle_session is not None and subtitle_session.running:
            return "subtitle_proposal"
        audio_session = getattr(self._gui, "_codex_audio_mix_session", None)
        if audio_session is not None and audio_session.running:
            return "audio_mix_proposal"
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
        context = build_audio_mix_context(
            self._gui.audioMixerChannels,
            preview_levels=self._gui.audioPreviewLevels,
            master_level=float(self._gui.audioMasterLevel),
            limiter_reduction_db=float(self._gui.audioLimiterReductionDb),
            playhead_seconds=float(self._gui.editorPlayhead.get("sourcePositionMs", 0)) / 1000.0,
        )
        return HandlerResult("audio mix state inspected", state=context)

    def _inspect_timeline(self, _args: Mapping[str, Any]) -> HandlerResult:
        return HandlerResult("timeline state inspected", state=deepcopy(dict(self._gui.cutTimeline)))

    def _inspect_processing(self, _args: Mapping[str, Any]) -> HandlerResult:
        if self.active_job == "highlight_analysis":
            state = {
                "active_job": self.active_job,
                "running": True,
                "progress": float(self._gui.highlightAnalysisProgress),
                "status": str(self._gui.highlightAnalysisState),
                "steps": [],
            }
        else:
            state = {
                "active_job": self.active_job,
                "running": bool(self._gui._running),
                "progress": float(self._gui._processing_progress.value),
                "status": str(self._gui._processing_progress.status),
                "steps": self._gui._processing_progress.as_list(),
            }
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

    def _propose_subtitle(self, args: Mapping[str, Any], _revision: int) -> HandlerResult:
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
        return HandlerResult(
            "audio mix proposal generation started",
            state={"status": "running"},
        )

    def _start_transcription(self, args: Mapping[str, Any]) -> HandlerResult:
        capabilities = self._gui.actionCapabilities
        if not capabilities.get("canTranscribe"):
            raise ActionRejected(
                ActionErrorCode.PRECONDITION_FAILED,
                str(capabilities.get("transcriptionReason") or "transcription is unavailable"),
            )
        self._gui.startTranscription(dict(self._gui.settings), args["mode"] == "replace")
        self._require_started_job("transcribe")
        return HandlerResult("transcription started", job={"type": "transcribe", "status": "running"})

    def _start_highlight(self, _args: Mapping[str, Any]) -> HandlerResult:
        if not self._gui.startHighlightAnalysis():
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "highlight analysis is unavailable")
        return HandlerResult("highlight analysis started", job={"type": "highlight_analysis", "status": "running"})

    def _render_normal(self, args: Mapping[str, Any]) -> HandlerResult:
        self._require_render("normal", args)
        self._gui.renderVideo(dict(self._gui.settings))
        self._require_started_job("render")
        return HandlerResult("normal render started", job={"type": "render", "status": "running"})

    def _render_short(self, args: Mapping[str, Any]) -> HandlerResult:
        self._require_render("short", args)
        self._gui.renderShortVideo()
        self._require_started_job("render_short")
        return HandlerResult("short render started", job={"type": "render_short", "status": "running"})

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
        if not bool(self._gui._running) or str(self._gui._active_job) != expected:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, f"{expected} job could not be started")


def build_gui_action_dispatcher(backend: Any) -> ActionDispatcher:
    return ActionDispatcher(GuiActionBackend(backend))
