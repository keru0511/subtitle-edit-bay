from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path, PureWindowsPath
import re
from collections.abc import Iterable, Mapping
from typing import TypedDict
from uuid import uuid4

from .application_logging import redact_text
from .data_boundary import coerce_float, is_object_dict, is_object_mapping, is_object_sequence, is_object_iterable


AUDIO_MIX_VERSION = 1
DEFAULT_AUDIO_TRACK = "0:a:0"
MAX_VOLUME_PERCENT = 200.0
AUDIO_MIX_MASTER_GAIN = 1.0
AUDIO_MIX_LIMITER_CEILING = 0.841395
AUDIO_MIX_MASTER_FILTER = (
    f"volume={AUDIO_MIX_MASTER_GAIN:.4f},"
    f"alimiter=limit={AUDIO_MIX_LIMITER_CEILING:.6f}:"
    "attack=5:release=80:level=disabled:latency=enabled"
)
AUDIO_SOURCE_ID_FIELD = "audio_channel_id"
AUDIO_CHANNEL_CHANGE_FIELDS = frozenset({"volume_percent", "muted", "solo", "enabled"})
_AUDIO_CHANNEL_ID_PATTERN = re.compile(r"audio:[0-9a-f]{32}\Z")


class AudioMixPayload(TypedDict):
    version: int
    customized: bool
    channels: list[dict[object, object]]


class AudioChannelView(TypedDict):
    id: str
    kind: str
    label: str
    enabled: bool
    muted: bool
    solo: bool
    volume_percent: float


class AudioMixError(ValueError):
    """Raised when a mixer update cannot be validated or applied."""


def _clamp_volume(value: object) -> float:
    try:
        numeric = coerce_float(value)
    except (TypeError, ValueError, OverflowError):
        numeric = 100.0
    if not math.isfinite(numeric):
        numeric = 100.0
    return max(0.0, min(MAX_VOLUME_PERCENT, numeric))


def is_opaque_audio_channel_id(value: object) -> bool:
    return bool(_AUDIO_CHANNEL_ID_PATTERN.fullmatch(str(value)))


def _opaque_channel_id(kind: str, identity: str) -> str:
    digest = sha256(f"subtitle-edit-bay\0{kind}\0{identity}".encode("utf-8")).hexdigest()[:32]
    return f"audio:{digest}"


def _video_channel_id(selector: str) -> str:
    return _opaque_channel_id("video", selector)


def _external_channel_id(
    source: dict[object, object],
    index: int,
    used_ids: set[str],
) -> str:
    del index
    persisted = str(source.get(AUDIO_SOURCE_ID_FIELD, "")).strip()
    if is_opaque_audio_channel_id(persisted) and persisted not in used_ids:
        used_ids.add(persisted)
        return persisted

    track_key = str(source.get("track_key", "")).strip()
    candidate = _opaque_channel_id("external", track_key) if track_key else ""
    if not candidate or candidate in used_ids:
        candidate = f"audio:{uuid4().hex}"
        while candidate in used_ids:
            candidate = f"audio:{uuid4().hex}"
    source[AUDIO_SOURCE_ID_FIELD] = candidate
    used_ids.add(candidate)
    return candidate


def _legacy_external_channel_id(source: Mapping[object, object], index: int) -> str:
    identity = str(source.get("track_key") or source.get("path") or source.get("file_name") or index)
    return f"external:{identity}"


def _looks_like_absolute_path(value: object) -> bool:
    text = str(value).strip()
    if not text:
        return False
    return text.casefold().startswith("file://") or Path(text).is_absolute() or PureWindowsPath(text).is_absolute()


def _safe_channel_label(value: object, fallback: str) -> str:
    label = "" if value is None else str(value).strip()
    return fallback if not label or _looks_like_absolute_path(label) else label


def validate_audio_channel_changes(changes: object) -> dict[str, float | bool]:
    if not is_object_mapping(changes) or not changes:
        raise AudioMixError("audio channel changes must be a non-empty object")
    unknown = sorted(str(key) for key in changes if not isinstance(key, str) or key not in AUDIO_CHANNEL_CHANGE_FIELDS)
    if unknown:
        raise AudioMixError("unsupported audio channel fields: " + ", ".join(unknown))
    validated: dict[str, float | bool] = {}
    for key in AUDIO_CHANNEL_CHANGE_FIELDS:
        if key not in changes:
            continue
        value = changes[key]
        if key == "volume_percent":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AudioMixError("volume_percent must be a number")
            number = float(value)
            if not math.isfinite(number) or not 0.0 <= number <= MAX_VOLUME_PERCENT:
                raise AudioMixError(f"volume_percent must be between 0 and {MAX_VOLUME_PERCENT:g}")
            validated[key] = number
        elif type(value) is not bool:
            raise AudioMixError(f"{key} must be true or false")
        else:
            validated[key] = value
    return validated


def update_audio_mix_channel(
    audio_mix: object,
    channel_id: str,
    changes: object,
) -> dict[object, object]:
    """Apply one validated channel update to a copy of the canonical mixer state."""

    if not isinstance(channel_id, str) or not is_opaque_audio_channel_id(channel_id):
        raise AudioMixError("audio channel id must be a safe opaque ID")
    validated = validate_audio_channel_changes(changes)
    candidate = deepcopy(dict(_mapping(audio_mix, "audio_mix")))
    channels = candidate.get("channels")
    if not isinstance(channels, list) or not is_object_sequence(channels):
        raise AudioMixError("audio mix channels must be an array")
    matches = [channel for channel in channels if is_object_dict(channel) and str(channel.get("id", "")) == channel_id]
    if len(matches) != 1:
        raise AudioMixError(f"audio channel does not exist or is ambiguous: {channel_id}")
    matches[0].update(validated)
    candidate["customized"] = True
    return candidate


def _path_free_channel_id(channel: dict[object, object]) -> str:
    channel_id = str(channel.get("id", "")).strip()
    if not is_opaque_audio_channel_id(channel_id):
        raise ValueError("audio channel identity is not a safe opaque ID")
    return channel_id


def path_free_audio_mix_channels(channels: Iterable[object]) -> list[AudioChannelView]:
    """Build the shared Codex-facing audio view from normalized mixer channels."""

    safe: list[AudioChannelView] = []
    channel_ids: set[str] = set()
    for index, channel in enumerate(channels):
        if not is_object_dict(channel):
            continue
        channel_id = _path_free_channel_id(channel)
        if channel_id in channel_ids:
            raise ValueError("audio channel identities must be unique")
        channel_ids.add(channel_id)
        kind = redact_text(channel.get("kind", ""), paths=True)[:40]
        safe.append(
            {
                "id": channel_id,
                "kind": kind,
                "label": redact_text(
                    _safe_channel_label(
                        channel.get("label"),
                        f"{'動画' if kind == 'video' else '外部'}音声 {index + 1}",
                    ),
                    paths=True,
                )[:160],
                "enabled": bool(channel.get("enabled", False)),
                "muted": bool(channel.get("muted", False)),
                "solo": bool(channel.get("solo", False)),
                "volume_percent": _clamp_volume(channel.get("volume_percent", 100.0)),
            }
        )
    return safe


def video_track_entries(streams: Iterable[object]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for audio_index, raw_stream in enumerate(streams):
        stream = _mapping(raw_stream, "audio stream")
        selector = f"0:a:{audio_index}"
        raw_tags = stream.get("tags")
        tags: dict[object, object] = raw_tags if is_object_dict(raw_tags) else {}
        title = str(tags.get("title", "")).strip()
        codec = str(stream.get("codec_name", "audio"))
        channels = stream.get("channels", "?")
        entries.append({"selector": selector, "label": f"{selector}  {title or f'{codec} / {channels}ch'}"})
    return entries


def _normalized_channel(channel: dict[object, object], defaults: dict[object, object]) -> dict[object, object]:
    normalized = {**defaults, **deepcopy(channel)}
    normalized["id"] = str(defaults["id"])
    normalized["kind"] = str(defaults["kind"])
    normalized["label"] = _safe_channel_label(
        normalized.get("label"),
        str(defaults["label"]),
    )
    normalized["enabled"] = bool(normalized.get("enabled", defaults.get("enabled", False)))
    normalized["muted"] = bool(normalized.get("muted", False))
    normalized["solo"] = bool(normalized.get("solo", False))
    normalized["volume_percent"] = _clamp_volume(normalized.get("volume_percent", 100.0))
    if defaults["kind"] == "video":
        normalized["selector"] = str(defaults["selector"])
        normalized.pop("path", None)
    else:
        normalized["path"] = str(defaults["path"])
        normalized.pop("selector", None)
    return normalized


def reconcile_audio_mix(
    project: object,
    video_tracks: Iterable[object] | None = None,
) -> AudioMixPayload:
    if not is_object_dict(project):
        raise AudioMixError("project must be an object")
    raw_current = project.get("audio_mix")
    current: dict[object, object] = raw_current if is_object_dict(raw_current) else {}
    raw_existing_channels = current.get("channels")
    existing_channels = (
        raw_existing_channels
        if isinstance(raw_existing_channels, list) and is_object_sequence(raw_existing_channels)
        else ()
    )
    existing_by_id = {
        str(channel.get("id")): channel
        for channel in existing_channels
        if is_object_dict(channel) and channel.get("id")
    }
    existing_video = [
        channel for channel in existing_channels if is_object_dict(channel) and channel.get("kind") == "video"
    ]
    existing_external = [
        channel for channel in existing_channels if is_object_dict(channel) and channel.get("kind") == "external"
    ]
    claimed_existing: set[int] = set()

    def existing_channel(*ids: str, kind: str, path: str = "") -> dict[object, object]:
        for channel_id in ids:
            candidate = existing_by_id.get(channel_id)
            if is_object_dict(candidate) and id(candidate) not in claimed_existing and candidate.get("kind") == kind:
                claimed_existing.add(id(candidate))
                return candidate
        if kind == "external" and path:
            for candidate in existing_external:
                if id(candidate) not in claimed_existing and str(candidate.get("path", "")) == path:
                    claimed_existing.add(id(candidate))
                    return candidate
        return {}

    supplied_tracks = None if video_tracks is None else [_mapping(track, "video track") for track in video_tracks]
    preserve_external = (
        supplied_tracks is None or bool(existing_video) or (not supplied_tracks and bool(existing_external))
    )

    preferred_selector = str(
        _mapping(project.get("render_settings", {}), "render_settings").get("output_audio_track") or DEFAULT_AUDIO_TRACK
    )
    if supplied_tracks is None:
        track_entries = [
            {"selector": str(channel.get("selector", "")), "label": str(channel.get("label", ""))}
            for channel in existing_video
            if str(channel.get("selector", "")).strip()
        ]
        if not track_entries and not project.get("audio_sources"):
            track_entries = [{"selector": preferred_selector, "label": preferred_selector}]
    else:
        track_entries = [
            {"selector": str(track.get("selector", "")), "label": str(track.get("label", ""))}
            for track in supplied_tracks
            if str(track.get("selector", "")).strip()
        ]

    selectors = {entry["selector"] for entry in track_entries}
    enabled_selector = (
        preferred_selector
        if preferred_selector in selectors
        else (track_entries[0]["selector"] if track_entries else "")
    )
    channels: list[dict[object, object]] = []
    used_channel_ids: set[str] = set()
    for entry in track_entries:
        selector = entry["selector"]
        channel_id = _video_channel_id(selector)
        used_channel_ids.add(channel_id)
        defaults: dict[object, object] = {
            "id": channel_id,
            "kind": "video",
            "label": entry["label"] or selector,
            "selector": selector,
            "enabled": selector == enabled_selector,
            "muted": False,
            "solo": False,
            "volume_percent": 100.0,
        }
        channels.append(
            _normalized_channel(
                existing_channel(channel_id, f"video:{selector}", kind="video"),
                defaults,
            )
        )

    for index, source in enumerate(_items(project.get("audio_sources", []), "audio_sources")):
        if not is_object_dict(source) or not str(source.get("path", "")).strip():
            continue
        channel_id = _external_channel_id(source, index, used_channel_ids)
        speaker_name = _safe_channel_label(
            source.get("name") or source.get("file_name"),
            f"外部音声 {index + 1}",
        )
        defaults = {
            "id": channel_id,
            "kind": "external",
            "label": speaker_name,
            "path": str(source["path"]),
            "enabled": False,
            "muted": False,
            "solo": False,
            "volume_percent": 100.0,
        }
        previous = existing_channel(
            channel_id,
            _legacy_external_channel_id(source, index),
            kind="external",
            path=str(source["path"]),
        )
        if previous and not preserve_external:
            previous = {}
        channels.append(_normalized_channel(previous, defaults))

    if not track_entries:
        has_enabled_external = any(
            bool(channel.get("enabled")) for channel in channels if channel.get("kind") == "external"
        )
        if not has_enabled_external:
            for channel in channels:
                if channel.get("kind") == "external":
                    channel["enabled"] = True
                    break

    audio_mix: AudioMixPayload = {
        "version": AUDIO_MIX_VERSION,
        "customized": bool(current.get("customized", False)),
        "channels": channels,
    }
    project["audio_mix"] = audio_mix
    return audio_mix


def reset_audio_mix(
    project: object,
    video_tracks: Iterable[object] | None = None,
) -> AudioMixPayload:
    if not is_object_dict(project):
        raise AudioMixError("project must be an object")
    project.pop("audio_mix", None)
    return reconcile_audio_mix(project, video_tracks)


def active_audio_mix_channels(audio_mix: object) -> list[dict[object, object]]:
    audio_mix = _mapping(audio_mix, "audio_mix")
    enabled = [
        deepcopy(channel)
        for channel in _items(audio_mix.get("channels", []), "audio_mix.channels")
        if is_object_dict(channel) and bool(channel.get("enabled")) and not bool(channel.get("muted"))
    ]
    solo = [channel for channel in enabled if bool(channel.get("solo"))]
    return solo or enabled


def build_audio_mix_filter(
    audio_mix: object,
    *,
    offset_seconds: float = 0.0,
    output_label: str = "mixed_audio",
    post_filter: str | None = None,
) -> tuple[list[str], str]:
    channels = active_audio_mix_channels(audio_mix)
    input_args: list[str] = []
    filters: list[str] = []
    branch_labels: list[str] = []
    external_input = 1
    for index, channel in enumerate(channels):
        if channel.get("kind") == "external":
            input_args.extend(["-i", str(channel.get("path", ""))])
            source_label = f"{external_input}:a:0"
            external_input += 1
        else:
            source_label = str(channel.get("selector") or DEFAULT_AUDIO_TRACK)
        chain = "aresample=48000:async=1:first_pts=0,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if channel.get("kind") == "external" and offset_seconds > 0:
            chain += f",adelay={round(offset_seconds * 1000)}:all=1"
        elif channel.get("kind") == "external" and offset_seconds < 0:
            chain += f",atrim=start={abs(offset_seconds):.3f},asetpts=PTS-STARTPTS"
        chain += f",volume={_clamp_volume(channel.get('volume_percent', 100.0)) / 100.0:.4f}"
        branch = f"mix_audio_{index}"
        filters.append(f"[{source_label}]{chain}[{branch}]")
        branch_labels.append(f"[{branch}]")

    base_label = "mix_audio_base"
    if not branch_labels:
        filters.append(f"anullsrc=channel_layout=stereo:sample_rate=48000[{base_label}]")
    elif len(branch_labels) == 1:
        filters.append(f"{branch_labels[0]}anull[{base_label}]")
    else:
        filters.append(
            f"{''.join(branch_labels)}amix=inputs={len(branch_labels)}:duration=longest:dropout_transition=0:normalize=0[{base_label}]"
        )
    final_filter = f"{post_filter},{AUDIO_MIX_MASTER_FILTER},apad" if post_filter else f"{AUDIO_MIX_MASTER_FILTER},apad"
    filters.append(f"[{base_label}]{final_filter}[{output_label}]")
    return input_args, ";".join(filters)


def _mapping(value: object, field: str) -> Mapping[object, object]:
    if not is_object_mapping(value):
        raise AudioMixError(f"{field} must be an object")
    return value


def _items(value: object, field: str) -> Iterable[object]:
    if not is_object_iterable(value):
        raise AudioMixError(f"{field} must be iterable")
    return value
