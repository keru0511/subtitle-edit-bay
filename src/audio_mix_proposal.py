from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re
from typing import Any, Iterable, Mapping, Sequence, cast

from .audio_mixer import (
    AUDIO_CHANNEL_CHANGE_FIELDS,
    MAX_VOLUME_PERCENT,
    AudioMixError,
    active_audio_mix_channels,
    is_opaque_audio_channel_id,
    update_audio_mix_channel,
    validate_audio_channel_changes,
)


AUDIO_MIX_PROPOSAL_SCHEMA_VERSION = 1
AUDIO_OPERATION_TYPE = "update_audio_channel"
AUDIO_CONTEXT_FIELDS = ("id", "kind", "label", "enabled", "muted", "solo", "volume_percent")
ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "summary",
        "warnings",
        "base_revision",
        "audio_state_revision",
        "operations",
    }
)
CODEX_OPERATION_FIELDS = frozenset({"id", "type", "channel_id", "changes", "reason"})
STORED_OPERATION_FIELDS = CODEX_OPERATION_FIELDS | {"before"}
_STATE_REVISION_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class AudioMixProposalError(ValueError):
    """Raised when a Codex audio proposal is invalid, unsafe, or stale."""


def _reject_unknown(value: Mapping[str, Any], allowed: frozenset[str], field: str) -> None:
    unknown = sorted(str(key) for key in value if not isinstance(key, str) or key not in allowed)
    if unknown:
        raise AudioMixProposalError(f"{field} contains unsupported fields: {', '.join(unknown)}")


def _bounded_level(value: object, *, maximum: float = 1.0) -> float:
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(maximum, number))


def _path_free_label(value: object, *, kind: str, index: int) -> str:
    label = "" if value is None else str(value).strip()
    is_path = (
        label.casefold().startswith("file://") or Path(label).is_absolute() or PureWindowsPath(label).is_absolute()
    )
    if label and not is_path:
        return label
    return f"{'動画' if kind == 'video' else '外部'}音声 {index + 1}"


def audio_mix_state_revision(channels: Sequence[Mapping[str, Any]]) -> str:
    safe = [{key: channel.get(key) for key in AUDIO_CONTEXT_FIELDS} for channel in channels]
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_audio_mix_context(
    channels: Sequence[Mapping[str, Any]],
    *,
    preview_levels: Mapping[str, float] | None = None,
    master_level: float = 0.0,
    limiter_reduction_db: float = 0.0,
    playhead_seconds: float | None = None,
    range_seconds: tuple[float, float] | None = None,
    project_revision: int | None = None,
) -> dict[str, Any]:
    """Build the complete path-free snapshot passed to Codex."""

    levels = preview_levels or {}
    safe_channels: list[dict[str, Any]] = []
    channel_ids: set[str] = set()
    for index, channel in enumerate(channels):
        if not isinstance(channel, Mapping):
            raise AudioMixProposalError(f"channels[{index}] must be an object")
        channel_id = str(channel.get("id", "")).strip()
        if not is_opaque_audio_channel_id(channel_id):
            raise AudioMixProposalError(f"channels[{index}].id is not a safe opaque audio ID")
        if channel_id in channel_ids:
            raise AudioMixProposalError(f"duplicate audio channel id: {channel_id}")
        channel_ids.add(channel_id)
        kind = str(channel.get("kind", ""))
        try:
            volume_percent = validate_audio_channel_changes({"volume_percent": channel.get("volume_percent", 100.0)})[
                "volume_percent"
            ]
        except AudioMixError as error:
            raise AudioMixProposalError(f"channels[{index}].volume_percent: {error}") from error
        safe_channels.append(
            {
                "id": channel_id,
                "kind": kind,
                "label": _path_free_label(channel.get("label"), kind=kind, index=index),
                "enabled": bool(channel.get("enabled", False)),
                "muted": bool(channel.get("muted", False)),
                "solo": bool(channel.get("solo", False)),
                "volume_percent": volume_percent,
                "preview_level": _bounded_level(levels.get(channel_id, 0.0)),
            }
        )
    context: dict[str, Any] = {
        "channels": safe_channels,
        "master_level": _bounded_level(master_level),
        "limiter_reduction_db": _bounded_level(limiter_reduction_db, maximum=float("inf")),
        "audio_state_revision": audio_mix_state_revision(channels),
    }
    if project_revision is not None:
        if type(project_revision) is not int or project_revision < 0:
            raise AudioMixProposalError("project_revision must be a non-negative integer")
        context["project_revision"] = project_revision
    if playhead_seconds is not None:
        context["playhead_seconds"] = _bounded_level(playhead_seconds, maximum=float("inf"))
    if range_seconds is not None:
        start = _bounded_level(range_seconds[0], maximum=float("inf"))
        end = _bounded_level(range_seconds[1], maximum=float("inf"))
        if end < start:
            raise AudioMixProposalError("audio context range must be increasing")
        context["range_seconds"] = [start, end]
    return context


def build_audio_mix_proposal_prompt(intent: str) -> str:
    requested = str(intent).strip()
    if not requested:
        raise AudioMixProposalError("audio mix intent must not be empty")
    return (
        f"音量ミキサーへの依頼: {requested}\n"
        "contextの現在値、preview_level、master_level、limiter_reduction_dbを根拠に、"
        "必要最小限のchannelだけを調整してください。固定増減ではなく現在のバランスから判断し、"
        "返答は指定schemaのProposalだけにしてください。contextのproject_revisionと"
        "audio_state_revisionはbase_revision/audio_state_revisionへそのまま設定してください。"
    )


def _validated_changes(value: object, field: str) -> dict[str, Any]:
    try:
        return validate_audio_channel_changes(value)
    except AudioMixError as error:
        raise AudioMixProposalError(f"{field}: {error}") from error


@dataclass(frozen=True)
class AudioMixProposalOperation:
    id: str
    channel_id: str
    changes: Mapping[str, Any]
    reason: str
    before: Mapping[str, Any] | None = None
    type: str = AUDIO_OPERATION_TYPE

    @classmethod
    def from_json(
        cls,
        payload: Mapping[str, Any],
        index: int,
        *,
        stored: bool,
    ) -> AudioMixProposalOperation:
        if not isinstance(payload, Mapping):
            raise AudioMixProposalError(f"operations[{index}] must be an object")
        _reject_unknown(
            payload,
            STORED_OPERATION_FIELDS if stored else CODEX_OPERATION_FIELDS,
            f"operations[{index}]",
        )
        operation_id = payload.get("id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise AudioMixProposalError(f"operations[{index}].id must be a non-empty string")
        if payload.get("type") != AUDIO_OPERATION_TYPE:
            raise AudioMixProposalError(f"unsupported audio operation type: {payload.get('type')!r}")
        channel_id = payload.get("channel_id")
        if not isinstance(channel_id, str) or not is_opaque_audio_channel_id(channel_id):
            raise AudioMixProposalError(f"operations[{index}].channel_id must be a safe opaque audio ID")
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise AudioMixProposalError(f"operations[{index}].reason must be a non-empty string")
        changes = _validated_changes(payload.get("changes"), f"operations[{index}].changes")
        before = None
        if stored:
            before = _validated_changes(payload.get("before"), f"operations[{index}].before")
            if set(before) != set(changes):
                raise AudioMixProposalError(f"operations[{index}].before fields must match changes fields")
        return cls(
            id=operation_id.strip(),
            channel_id=channel_id,
            changes=changes,
            reason=reason.strip(),
            before=before,
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "channel_id": self.channel_id,
            "changes": deepcopy(dict(self.changes)),
            "reason": self.reason,
        }
        if self.before is not None:
            payload["before"] = deepcopy(dict(self.before))
        return payload


@dataclass(frozen=True)
class AudioMixProposal:
    summary: str
    warnings: tuple[str, ...]
    base_revision: int
    audio_state_revision: str
    operations: tuple[AudioMixProposalOperation, ...]
    schema_version: int = AUDIO_MIX_PROPOSAL_SCHEMA_VERSION

    @classmethod
    def from_json(
        cls,
        payload: Mapping[str, Any],
        *,
        stored: bool = False,
    ) -> AudioMixProposal:
        if not isinstance(payload, Mapping):
            raise AudioMixProposalError("proposal must be an object")
        _reject_unknown(payload, ROOT_FIELDS, "proposal")
        schema_version = payload.get("schema_version")
        if type(schema_version) is not int or schema_version != AUDIO_MIX_PROPOSAL_SCHEMA_VERSION:
            raise AudioMixProposalError(f"proposal.schema_version must be {AUDIO_MIX_PROPOSAL_SCHEMA_VERSION}")
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise AudioMixProposalError("proposal.summary must be a non-empty string")
        warnings = payload.get("warnings")
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            raise AudioMixProposalError("proposal.warnings must be an array of strings")
        base_revision = payload.get("base_revision")
        if type(base_revision) is not int or base_revision < 0:
            raise AudioMixProposalError("proposal.base_revision must be a non-negative integer")
        state_revision = payload.get("audio_state_revision")
        if not isinstance(state_revision, str) or not _STATE_REVISION_PATTERN.fullmatch(state_revision):
            raise AudioMixProposalError("proposal.audio_state_revision must be a SHA-256 revision")
        raw_operations = payload.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise AudioMixProposalError("proposal.operations must be a non-empty array")
        operations = tuple(
            AudioMixProposalOperation.from_json(item, index, stored=stored) for index, item in enumerate(raw_operations)
        )
        operation_ids = [operation.id for operation in operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise AudioMixProposalError("audio operation ids must be unique")
        channel_ids = [operation.channel_id for operation in operations]
        if len(channel_ids) != len(set(channel_ids)):
            raise AudioMixProposalError("audio proposal must contain at most one operation per channel")
        return cls(
            summary=summary.strip(),
            warnings=tuple(warnings),
            base_revision=base_revision,
            audio_state_revision=state_revision,
            operations=operations,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "summary": self.summary,
            "warnings": list(self.warnings),
            "base_revision": self.base_revision,
            "audio_state_revision": self.audio_state_revision,
            "operations": [operation.to_json() for operation in self.operations],
        }


def _channels_by_id(
    channels: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, channel in enumerate(channels):
        if not isinstance(channel, Mapping):
            raise AudioMixProposalError(f"audio channels[{index}] must be an object")
        channel_id = str(channel.get("id", ""))
        if not is_opaque_audio_channel_id(channel_id):
            raise AudioMixProposalError(f"audio channels[{index}].id is not a safe opaque audio ID")
        if channel_id in by_id:
            raise AudioMixProposalError(f"duplicate audio channel id: {channel_id}")
        by_id[channel_id] = channel
    return by_id


def _require_current_state(
    proposal: AudioMixProposal,
    channels: Sequence[Mapping[str, Any]],
    *,
    current_revision: int,
) -> dict[str, Mapping[str, Any]]:
    if proposal.base_revision != current_revision:
        raise AudioMixProposalError("audio mix proposal is stale")
    if proposal.audio_state_revision != audio_mix_state_revision(channels):
        raise AudioMixProposalError("audio mix state changed after proposal generation")
    by_id = _channels_by_id(channels)
    for operation in proposal.operations:
        channel = by_id.get(operation.channel_id)
        if channel is None:
            raise AudioMixProposalError(f"audio channel no longer exists: {operation.channel_id}")
        if operation.before is not None and any(
            operation.before.get(key) != channel.get(key) for key in operation.changes
        ):
            raise AudioMixProposalError(f"audio channel changed after proposal generation: {operation.channel_id}")
    return by_id


def build_audio_mix_proposal(
    codex_output: Mapping[str, Any],
    channels: Sequence[Mapping[str, Any]],
    *,
    project_revision: int,
) -> dict[str, Any]:
    """Validate Codex output against its source snapshot and add backend-owned before values."""

    parsed = AudioMixProposal.from_json(codex_output)
    by_id = _require_current_state(parsed, channels, current_revision=project_revision)
    operations = tuple(
        replace(
            operation,
            before={key: deepcopy(by_id[operation.channel_id].get(key)) for key in operation.changes},
        )
        for operation in parsed.operations
    )
    return replace(parsed, operations=operations).to_json()


def apply_audio_mix_proposal(
    audio_mix: Mapping[str, Any],
    proposal: Mapping[str, Any],
    *,
    current_revision: int,
    selected_operation_ids: Iterable[str] | None = None,
    allow_silence: bool = False,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    parsed = AudioMixProposal.from_json(proposal, stored=True)
    channels = audio_mix.get("channels")
    if not isinstance(channels, list):
        raise AudioMixProposalError("audio mix channels must be an array")
    _require_current_state(parsed, channels, current_revision=current_revision)

    selected = set(selected_operation_ids) if selected_operation_ids is not None else None
    operation_ids = {operation.id for operation in parsed.operations}
    if selected is not None:
        unknown_selected = selected - operation_ids
        if unknown_selected:
            raise AudioMixProposalError("selected audio operation does not exist")
    operations = [operation for operation in parsed.operations if selected is None or operation.id in selected]
    if not operations:
        raise AudioMixProposalError("no audio operations were selected")

    updated = deepcopy(dict(audio_mix))
    changed_ids: list[str] = []
    try:
        for operation in operations:
            updated = update_audio_mix_channel(
                updated,
                operation.channel_id,
                operation.changes,
            )
            changed_ids.append(operation.channel_id)
    except AudioMixError as error:
        raise AudioMixProposalError(str(error)) from error

    if not allow_silence and not active_audio_mix_channels(updated):
        raise AudioMixProposalError("audio changes would disable or mute every output channel")
    return updated, tuple(changed_ids)


_AUDIO_CHANGE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "minProperties": 1,
    "properties": {
        "volume_percent": {"type": "number", "minimum": 0, "maximum": MAX_VOLUME_PERCENT},
        "muted": {"type": "boolean"},
        "solo": {"type": "boolean"},
        "enabled": {"type": "boolean"},
    },
}

AUDIO_MIX_PROPOSAL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "summary",
        "warnings",
        "base_revision",
        "audio_state_revision",
        "operations",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": AUDIO_MIX_PROPOSAL_SCHEMA_VERSION},
        "summary": {"type": "string", "minLength": 1},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "base_revision": {"type": "integer", "minimum": 0},
        "audio_state_revision": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "operations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "type", "channel_id", "changes", "reason"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "const": AUDIO_OPERATION_TYPE},
                    "channel_id": {
                        "type": "string",
                        "pattern": "^audio:[0-9a-f]{32}$",
                    },
                    "changes": _AUDIO_CHANGE_OUTPUT_SCHEMA,
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


assert set(_AUDIO_CHANGE_OUTPUT_SCHEMA["properties"]) == set(AUDIO_CHANNEL_CHANGE_FIELDS)
