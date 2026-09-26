from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from typing import Callable, Mapping, Protocol, cast

from .audio_mix_proposal import AudioMixProposalError, build_audio_mix_context
from .audio_mixer import is_opaque_audio_channel_id
from .data_boundary import coerce_float, is_object_iterable, is_object_mapping, is_object_sequence

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
    args: Mapping[str, object]
    scope_id: str
    project_revision: int | None = None
    schema_version: int = ACTION_SCHEMA_VERSION

    @classmethod
    def from_json(cls, payload: object) -> "ActionRequest":
        if not is_object_mapping(payload):
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "action must be an object")
        allowed = {"schema_version", "kind", "type", "args", "scope_id", "project_revision"}
        unknown = sorted(str(key) for key in payload if key not in allowed)
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
        kind_value = payload.get("kind")
        try:
            kind = ActionKind(kind_value)
        except ValueError as error:
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "kind must be inspect, propose, or execute") from error
        action_type = payload.get("type")
        if not isinstance(action_type, str) or not action_type.strip():
            raise ActionRejected(ActionErrorCode.INVALID_SCHEMA, "type must be a non-empty string")
        args = payload.get("args")
        if not is_object_mapping(args) or not all(isinstance(key, str) for key in args):
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
            args=deepcopy({key: value for key, value in args.items() if isinstance(key, str)}),
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
    types: tuple[type[object], ...]
    required: bool = False
    choices: frozenset[object] | None = None
    minimum: float | None = None
    maximum: float | None = None
    non_empty: bool = False


DestructivePredicate = Callable[[Mapping[str, object]], bool]


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
    state: Mapping[str, object] | None = None
    proposal: Mapping[str, object] | None = None
    job: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ActionResult:
    status: ActionStatus
    action_type: str = ""
    code: str = ""
    message: str = ""
    revision: int | None = None
    current_state: Mapping[str, object] | None = None
    proposal: Mapping[str, object] | None = None
    job: Mapping[str, object] | None = None

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
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

    def inspect(self, action_type: str, args: Mapping[str, object]) -> HandlerResult: ...

    def propose(self, action_type: str, args: Mapping[str, object], revision: int) -> HandlerResult: ...

    def execute(self, action_type: str, args: Mapping[str, object]) -> HandlerResult: ...


class _ProposalSnapshot(Protocol):
    state: str


class _ProposalSession(Protocol):
    running: bool
    snapshot: _ProposalSnapshot


class _ProcessingProgress(Protocol):
    value: float
    status: str

    def as_list(self) -> list[dict[str, object]]: ...


class _WorkflowProgress(Protocol):
    @property
    def processing_progress(self) -> _ProcessingProgress: ...


class _DependencyState(Protocol):
    ready: bool
    ffmpeg: bool
    ffprobe: bool
    whisperx: bool
    cuda: bool
    nvenc: bool


class _GuiActionSurface(Protocol):
    """Codex操作が参照するGUIの固定インターフェース。"""

    _project_revision: int
    _running: bool
    _active_job: str
    _project: Mapping[str, object] | None
    _project_dirty: bool
    _selected_segment_index: int
    _codex_session: _ProposalSession
    _codex_audio_mix_session: _ProposalSession
    _processing_progress: _ProcessingProgress
    workflow: _WorkflowProgress
    _dependencies: _DependencyState
    highlightAnalysisState: str
    highlightAnalysisProgress: float
    subtitleSegments: list[Mapping[str, object]]
    cutTimeline: Mapping[str, object]
    editorPlayhead: Mapping[str, object]
    actionCapabilities: Mapping[str, object]
    settings: Mapping[str, object]

    def startCodexEdit(self, intent: str, scope: str, range_start: float, range_end: float) -> None: ...

    def start_codex_audio_mix_proposal(self, *, intent: str, revision: int, context: Mapping[str, object]) -> bool: ...

    def transcribeProject(self, settings: dict[str, object], mode: str) -> None: ...

    def startHighlightAnalysis(self) -> bool: ...

    def renderVideo(self, settings: dict[str, object]) -> None: ...

    def renderShortVideo(self) -> None: ...

    def codex_render_output_exists(self, *, short: bool) -> bool: ...


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

    def dispatch(self, payload: object, *, trusted_scope: ActionScope) -> ActionResult:
        action_type = str(payload.get("type", "")) if is_object_mapping(payload) else ""
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
    def _validate_args(args: Mapping[str, object], fields: Mapping[str, FieldSchema]) -> None:
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
            if start is None or end is None or coerce_float(end) <= coerce_float(start):
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

    def __init__(self, backend: object) -> None:
        self._gui = cast(_GuiActionSurface, backend)
        self._inspect_handlers: Mapping[str, Callable[[Mapping[str, object]], HandlerResult]] = {
            "inspect_project_state": self._inspect_project,
            "inspect_subtitle_state": self._inspect_subtitles,
            "inspect_audio_mix_state": self._inspect_audio,
            "inspect_timeline_state": self._inspect_timeline,
            "inspect_processing_state": self._inspect_processing,
            "inspect_dependency_state": self._inspect_dependencies,
            "inspect_render_state": self._inspect_render,
            "inspect_selection_state": self._inspect_selection,
        }
        self._propose_handlers: Mapping[str, Callable[[Mapping[str, object], int], HandlerResult]] = {
            "propose_subtitle_edit": self._propose_subtitle,
            "propose_audio_mix": self._propose_audio,
        }
        self._execute_handlers: Mapping[str, Callable[[Mapping[str, object]], HandlerResult]] = {
            "start_transcription": self._start_transcription,
            "start_highlight_analysis": self._start_highlight,
            "render_normal": self._render_normal,
            "render_short": self._render_short,
        }

    def _optional_gui_value(self, name: str, default: object = None) -> object:
        return cast(object, getattr(self._gui, name, default))

    def _optional_session(self, name: str) -> _ProposalSession | None:
        value = self._optional_gui_value(name)
        return None if value is None else cast(_ProposalSession, value)

    @property
    def current_revision(self) -> int:
        return int(self._gui._project_revision)

    @property
    def active_job(self) -> str:
        if bool(self._gui._running):
            return str(self._gui._active_job or "processing")
        if str(self._gui.highlightAnalysisState) in {"running", "cancelling"}:
            return "highlight_analysis"
        codex_session = self._optional_session("_codex_session")
        if codex_session is not None and bool(codex_session.running):
            return "subtitle_proposal"
        audio_session = self._optional_session("_codex_audio_mix_session")
        if audio_session is not None and bool(audio_session.running):
            return "audio_mix_proposal"
        return ""

    def inspect(self, action_type: str, args: Mapping[str, object]) -> HandlerResult:
        handler = self._inspect_handlers.get(action_type)
        if handler is None:
            raise ActionRejected(ActionErrorCode.UNKNOWN_ACTION, "inspect action has no backend handler")
        return handler(args)

    def propose(self, action_type: str, args: Mapping[str, object], revision: int) -> HandlerResult:
        handler = self._propose_handlers.get(action_type)
        if handler is None:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "proposal domain is not available")
        return handler(args, revision)

    def execute(self, action_type: str, args: Mapping[str, object]) -> HandlerResult:
        handler = self._execute_handlers.get(action_type)
        if handler is None:
            raise ActionRejected(ActionErrorCode.UNKNOWN_ACTION, "execute action has no backend handler")
        return handler(args)

    def _inspect_project(self, _args: Mapping[str, object]) -> HandlerResult:
        project = self._gui._project
        segments = project.get("segments", []) if project else []
        state = {
            "loaded": project is not None,
            "dirty": bool(self._gui._project_dirty),
            "revision": self.current_revision,
            "segment_count": len(segments) if is_object_sequence(segments) else 0,
            "has_video": bool(project and project.get("video")),
        }
        return HandlerResult("project state inspected", state=state)

    def _inspect_subtitles(self, _args: Mapping[str, object]) -> HandlerResult:
        segments = self._gui.subtitleSegments
        safe_fields = {"id", "start", "end", "text", "speaker", "emphasis", "position"}
        safe = [{key: value for key, value in item.items() if key in safe_fields} for item in segments]
        return HandlerResult("subtitle state inspected", state={"segments": safe})

    def _inspect_audio(self, _args: Mapping[str, object]) -> HandlerResult:
        raw_channels = self._optional_gui_value("audioMixerChannels", ())
        if not is_object_iterable(raw_channels):
            raise TypeError("audioMixerChannels must be iterable")
        channels = list(raw_channels)
        raw_preview_levels = self._optional_gui_value("audioPreviewLevels", {})
        preview_levels = (
            {key: value for key, value in raw_preview_levels.items() if isinstance(key, str)}
            if is_object_mapping(raw_preview_levels)
            else {}
        )
        try:
            master_level = coerce_float(self._optional_gui_value("audioMasterLevel", 0.0))
        except (TypeError, ValueError, OverflowError):
            master_level = 0.0
        try:
            limiter_reduction_db = coerce_float(self._optional_gui_value("audioLimiterReductionDb", 0.0))
        except (TypeError, ValueError, OverflowError):
            limiter_reduction_db = 0.0
        playhead = self._optional_gui_value("editorPlayhead", {})
        try:
            playhead_seconds = (
                coerce_float(playhead.get("sourcePositionMs", 0)) / 1000.0 if is_object_mapping(playhead) else 0.0
            )
        except (TypeError, ValueError, OverflowError):
            playhead_seconds = 0.0
        try:
            context = build_audio_mix_context(
                channels,
                preview_levels=preview_levels,
                master_level=master_level,
                limiter_reduction_db=limiter_reduction_db,
                playhead_seconds=playhead_seconds,
                project_revision=self.current_revision,
            )
        except AudioMixProposalError:
            # Keep inspect useful for older GUI adapters while never exposing a
            # legacy/path-derived identifier to Codex.
            safe_channels: list[dict[str, object]] = []
            for index, item in enumerate(channels):
                if not is_object_mapping(item):
                    continue
                channel = {key: value for key, value in item.items() if isinstance(key, str)}
                channel_id = str(channel.get("id", "")).strip()
                if not is_opaque_audio_channel_id(channel_id):
                    identity = "|".join(
                        (
                            str(index),
                            str(channel.get("kind", "")),
                            str(channel.get("label", "")),
                        )
                    )
                    channel["id"] = "audio:" + sha256(identity.encode("utf-8")).hexdigest()[:32]
                safe_channels.append(channel)
            try:
                context = build_audio_mix_context(
                    safe_channels,
                    preview_levels=preview_levels,
                    master_level=master_level,
                    limiter_reduction_db=limiter_reduction_db,
                    playhead_seconds=playhead_seconds,
                    project_revision=self.current_revision,
                )
            except AudioMixProposalError:
                context = {
                    "channels": [],
                    "master_level": 0.0,
                    "limiter_reduction_db": 0.0,
                    "audio_state_revision": "sha256:" + "0" * 64,
                    "project_revision": self.current_revision,
                }
        return HandlerResult("audio mix state inspected", state=context)

    def _inspect_timeline(self, _args: Mapping[str, object]) -> HandlerResult:
        return HandlerResult("timeline state inspected", state=deepcopy(dict(self._gui.cutTimeline)))

    def _inspect_processing(self, _args: Mapping[str, object]) -> HandlerResult:
        active_job = self.active_job
        state: dict[str, object]
        if active_job == "highlight_analysis":
            state = {
                "active_job": active_job,
                "running": True,
                "progress": float(self._gui.highlightAnalysisProgress),
                "status": str(self._gui.highlightAnalysisState),
                "steps": [],
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
        elif active_job == "audio_mix_proposal":
            snapshot = self._gui._codex_audio_mix_session.snapshot
            state = {
                "active_job": active_job,
                "running": bool(self._gui._codex_audio_mix_session.running),
                "progress": None,
                "progress_known": False,
                "status": str(snapshot.state),
                "steps": [],
            }
        else:
            progress = self._gui.workflow.processing_progress
            state = {
                "active_job": active_job,
                "running": bool(self._gui._running),
                "progress": float(progress.value),
                "status": str(progress.status),
                "steps": progress.as_list(),
            }
        return HandlerResult("processing state inspected", state=state)

    def _inspect_dependencies(self, _args: Mapping[str, object]) -> HandlerResult:
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

    def _inspect_render(self, _args: Mapping[str, object]) -> HandlerResult:
        return HandlerResult("render state inspected", state=deepcopy(dict(self._gui.actionCapabilities)))

    def _inspect_selection(self, _args: Mapping[str, object]) -> HandlerResult:
        segment_id = ""
        index = int(self._gui._selected_segment_index)
        raw_segments = self._gui._project.get("segments", []) if self._gui._project else []
        segments = raw_segments if is_object_sequence(raw_segments) else []
        if 0 <= index < len(segments):
            segment = segments[index]
            if is_object_mapping(segment):
                segment_id = str(segment.get("id", ""))
        return HandlerResult(
            "selection state inspected",
            state={"segment_id": segment_id, "playhead": deepcopy(dict(self._gui.editorPlayhead))},
        )

    def _propose_subtitle(self, args: Mapping[str, object], _revision: int) -> HandlerResult:
        if bool(self._gui._codex_session.running):
            raise ActionRejected(ActionErrorCode.JOB_CONFLICT, "a subtitle proposal is already being generated")
        self._gui.startCodexEdit(
            str(args["intent"]),
            str(args["selection_scope"]),
            coerce_float(args.get("range_start", 0.0)),
            coerce_float(args.get("range_end", 0.0)),
        )
        if not self._gui._codex_session.running:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "subtitle proposal could not be started")
        return HandlerResult("subtitle proposal generation started", state={"status": "running"})

    def _propose_audio(self, args: Mapping[str, object], revision: int) -> HandlerResult:
        if self.active_job:
            raise ActionRejected(ActionErrorCode.JOB_CONFLICT, "another job or proposal is already running")
        inspected = self._inspect_audio({})
        if inspected.state is None:
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "audio mix state could not be inspected")
        if not self._gui.start_codex_audio_mix_proposal(
            intent=str(args["intent"]),
            revision=revision,
            context=inspected.state,
        ):
            raise ActionRejected(
                ActionErrorCode.PRECONDITION_FAILED,
                "audio mix proposal could not be started",
            )
        return HandlerResult("audio mix proposal generation started", state={"status": "running"})

    def _start_transcription(self, args: Mapping[str, object]) -> HandlerResult:
        capabilities = self._gui.actionCapabilities
        if not capabilities.get("canTranscribe"):
            raise ActionRejected(
                ActionErrorCode.PRECONDITION_FAILED,
                str(capabilities.get("transcriptionReason") or "transcription is unavailable"),
            )
        self._gui.transcribeProject(dict(self._gui.settings), str(args["mode"]))
        self._require_started_job("transcribe")
        return HandlerResult("transcription started", job={"type": "transcribe", "status": "running"})

    def _start_highlight(self, _args: Mapping[str, object]) -> HandlerResult:
        if not self._gui.startHighlightAnalysis():
            raise ActionRejected(ActionErrorCode.PRECONDITION_FAILED, "highlight analysis is unavailable")
        return HandlerResult("highlight analysis started", job={"type": "highlight_analysis", "status": "running"})

    def _render_normal(self, args: Mapping[str, object]) -> HandlerResult:
        self._require_render("normal", args)
        self._gui.renderVideo(dict(self._gui.settings))
        self._require_started_job("render")
        return HandlerResult("normal render started", job={"type": "render", "status": "running"})

    def _render_short(self, args: Mapping[str, object]) -> HandlerResult:
        self._require_render("short", args)
        self._gui.renderShortVideo()
        self._require_started_job("render_short")
        return HandlerResult("short render started", job={"type": "render_short", "status": "running"})

    def _require_render(self, kind: str, args: Mapping[str, object]) -> None:
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


def build_gui_action_dispatcher(backend: object) -> ActionDispatcher:
    return ActionDispatcher(GuiActionBackend(backend))
