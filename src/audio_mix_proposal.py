from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from .audio_mixer import MAX_VOLUME_PERCENT, active_audio_mix_channels


AUDIO_OPERATION_TYPE = "update_audio_channel"
AUDIO_CHANGE_FIELDS = frozenset({"volume_percent", "muted", "solo", "enabled"})
AUDIO_CONTEXT_FIELDS = ("id", "kind", "label", "enabled", "muted", "solo", "volume_percent")


class AudioMixProposalError(ValueError):
    pass


def audio_mix_state_revision(channels: Sequence[Mapping[str, Any]]) -> str:
    safe = [{key: channel.get(key) for key in AUDIO_CONTEXT_FIELDS} for channel in channels]
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_audio_mix_context(
    channels: Sequence[Mapping[str, Any]],
    *,
    preview_levels: Mapping[str, float] | None = None,
    master_level: float = 0.0,
    limiter_reduction_db: float = 0.0,
    playhead_seconds: float | None = None,
    range_seconds: tuple[float, float] | None = None,
) -> dict[str, Any]:
    levels = preview_levels or {}
    safe_channels: list[dict[str, Any]] = []
    for channel in channels:
        channel_id = str(channel.get("id", ""))
        safe = {key: deepcopy(channel.get(key)) for key in AUDIO_CONTEXT_FIELDS}
        safe["preview_level"] = max(0.0, min(1.0, float(levels.get(channel_id, 0.0))))
        safe_channels.append(safe)
    context: dict[str, Any] = {
        "channels": safe_channels,
        "master_level": max(0.0, min(1.0, float(master_level))),
        "limiter_reduction_db": max(0.0, float(limiter_reduction_db)),
        "audio_state_revision": audio_mix_state_revision(channels),
    }
    if playhead_seconds is not None:
        context["playhead_seconds"] = max(0.0, float(playhead_seconds))
    if range_seconds is not None:
        context["range_seconds"] = [max(0.0, float(range_seconds[0])), max(0.0, float(range_seconds[1]))]
    return context


def _target_channels(intent: str, channels: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    normalized = intent.casefold()
    bgm_terms = ("bgm", "music", "音楽")
    voice_terms = ("voice", "vocal", "mic", "speaker", "声", "音声", "話者", "マイク", "実況")
    wants_bgm = any(term in normalized for term in bgm_terms)
    wants_voice = any(term in normalized for term in voice_terms)

    def label(channel: Mapping[str, Any]) -> str:
        return str(channel.get("label", "")).casefold()

    if wants_bgm:
        return [channel for channel in channels if any(term in label(channel) for term in bgm_terms)]
    if wants_voice:
        explicit = [channel for channel in channels if any(term in label(channel) for term in voice_terms)]
        if explicit:
            return explicit
        external = [channel for channel in channels if channel.get("kind") == "external"]
        return external if len(external) == 1 else []
    mentioned = [channel for channel in channels if label(channel) and label(channel) in normalized]
    return mentioned


def _requested_changes(intent: str, channel: Mapping[str, Any]) -> dict[str, Any]:
    normalized = intent.casefold()
    changes: dict[str, Any] = {}
    if any(term in normalized for term in ("ミュート解除", "unmute")):
        changes["muted"] = False
    elif any(term in normalized for term in ("ミュート", "mute", "消音")):
        changes["muted"] = True
    if any(term in normalized for term in ("ソロ解除", "unsolo")):
        changes["solo"] = False
    elif any(term in normalized for term in ("ソロ", "solo")):
        changes["solo"] = True
    if any(term in normalized for term in ("無効", "オフ", "disable")):
        changes["enabled"] = False
    elif any(term in normalized for term in ("有効", "オン", "enable")):
        changes["enabled"] = True

    current = float(channel.get("volume_percent", 100.0))
    if any(term in normalized for term in ("少し下げ", "slightly lower", "a little lower")):
        changes["volume_percent"] = max(0.0, current - 15.0)
    elif any(term in normalized for term in ("下げ", "小さく", "lower", "quieter")):
        changes["volume_percent"] = max(0.0, current - 25.0)
    elif any(term in normalized for term in ("少し上げ", "slightly raise", "a little louder")):
        changes["volume_percent"] = min(MAX_VOLUME_PERCENT, current + 10.0)
    elif any(term in normalized for term in ("上げ", "大きく", "raise", "louder")):
        changes["volume_percent"] = min(MAX_VOLUME_PERCENT, current + 20.0)
    return changes


def build_audio_mix_proposal(
    intent: str,
    channels: Sequence[Mapping[str, Any]],
    *,
    project_revision: int,
) -> dict[str, Any]:
    if not intent.strip():
        raise AudioMixProposalError("audio mix intent must not be empty")
    targets = _target_channels(intent, channels)
    if not targets:
        raise AudioMixProposalError("audio mix intent does not identify a unique channel")
    operations: list[dict[str, Any]] = []
    for index, channel in enumerate(targets, start=1):
        channel_id = str(channel.get("id", "")).strip()
        if not channel_id:
            raise AudioMixProposalError("audio channel id must not be empty")
        changes = _requested_changes(intent, channel)
        if not changes:
            raise AudioMixProposalError("audio mix intent does not contain a supported change")
        before = {key: deepcopy(channel.get(key)) for key in changes}
        operations.append(
            {
                "id": f"audio-{index:03d}",
                "type": AUDIO_OPERATION_TYPE,
                "channel_id": channel_id,
                "changes": changes,
                "before": before,
                "reason": intent.strip(),
            }
        )
    return {
        "summary": f"音量ミキサー変更を{len(operations)}件提案します",
        "base_revision": project_revision,
        "audio_state_revision": audio_mix_state_revision(channels),
        "operations": operations,
    }


def _validate_changes(changes: object) -> dict[str, Any]:
    if not isinstance(changes, Mapping) or not changes:
        raise AudioMixProposalError("audio operation changes must be a non-empty object")
    unknown = sorted(set(changes) - AUDIO_CHANGE_FIELDS)
    if unknown:
        raise AudioMixProposalError("unsupported audio change fields: " + ", ".join(unknown))
    validated: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "volume_percent":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AudioMixProposalError("volume_percent must be a number")
            number = float(value)
            if not math.isfinite(number) or not 0.0 <= number <= MAX_VOLUME_PERCENT:
                raise AudioMixProposalError(f"volume_percent must be between 0 and {MAX_VOLUME_PERCENT:g}")
            validated[key] = number
        elif type(value) is not bool:
            raise AudioMixProposalError(f"{key} must be true or false")
        else:
            validated[key] = value
    return validated


def apply_audio_mix_proposal(
    audio_mix: Mapping[str, Any],
    proposal: Mapping[str, Any],
    *,
    current_revision: int,
    selected_operation_ids: set[str] | None = None,
    allow_silence: bool = False,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if proposal.get("base_revision") != current_revision:
        raise AudioMixProposalError("audio mix proposal is stale")
    channels = audio_mix.get("channels")
    if not isinstance(channels, list):
        raise AudioMixProposalError("audio mix channels must be an array")
    if proposal.get("audio_state_revision") != audio_mix_state_revision(channels):
        raise AudioMixProposalError("audio mix state changed after proposal generation")
    operations = proposal.get("operations")
    if not isinstance(operations, list) or not operations:
        raise AudioMixProposalError("audio mix proposal operations must be a non-empty array")

    updated = deepcopy(dict(audio_mix))
    updated_channels = updated["channels"]
    by_id = {
        str(channel.get("id")): channel
        for channel in updated_channels
        if isinstance(channel, dict) and str(channel.get("id", "")).strip()
    }
    operation_ids: set[str] = set()
    changed_ids: list[str] = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise AudioMixProposalError("audio operation must be an object")
        operation_id = str(operation.get("id", "")).strip()
        if not operation_id or operation_id in operation_ids:
            raise AudioMixProposalError("audio operation ids must be non-empty and unique")
        operation_ids.add(operation_id)
        if selected_operation_ids is not None and operation_id not in selected_operation_ids:
            continue
        if operation.get("type") != AUDIO_OPERATION_TYPE:
            raise AudioMixProposalError("unsupported audio operation type")
        channel_id = str(operation.get("channel_id", "")).strip()
        channel = by_id.get(channel_id)
        if channel is None:
            raise AudioMixProposalError(f"audio channel no longer exists: {channel_id}")
        changes = _validate_changes(operation.get("changes"))
        before = operation.get("before")
        if not isinstance(before, Mapping) or any(before.get(key) != channel.get(key) for key in changes):
            raise AudioMixProposalError(f"audio channel changed after proposal generation: {channel_id}")
        channel.update(changes)
        changed_ids.append(channel_id)

    if selected_operation_ids is not None:
        unknown_selected = selected_operation_ids - operation_ids
        if unknown_selected:
            raise AudioMixProposalError("selected audio operation does not exist")
    if not changed_ids:
        raise AudioMixProposalError("no audio operations were selected")
    if not allow_silence and not active_audio_mix_channels(updated):
        raise AudioMixProposalError("audio changes would disable or mute every output channel")
    updated["customized"] = True
    return updated, tuple(changed_ids)
