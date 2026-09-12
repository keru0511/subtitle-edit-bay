from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path, PureWindowsPath
import re
from typing import Any, Iterable, cast
from uuid import uuid4

from .application_logging import redact_text


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
_AUDIO_CHANNEL_ID_PATTERN = re.compile(r"audio:[0-9a-f]{32}\Z")


def _clamp_volume(value: object) -> float:
    try:
        numeric = float(cast(Any, value))
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
    source: dict[str, Any],
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


def _legacy_external_channel_id(source: dict[str, Any], index: int) -> str:
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


def _path_free_channel_id(channel: dict[str, Any]) -> str:
    channel_id = str(channel.get("id", "")).strip()
    if not is_opaque_audio_channel_id(channel_id):
        raise ValueError("audio channel identity is not a safe opaque ID")
    return channel_id


def path_free_audio_mix_channels(channels: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the shared Codex-facing audio view from normalized mixer channels."""

    safe: list[dict[str, Any]] = []
    channel_ids: set[str] = set()
    for index, channel in enumerate(channels):
        if not isinstance(channel, dict):
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


def video_track_entries(streams: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for audio_index, stream in enumerate(streams):
        selector = f"0:a:{audio_index}"
        raw_tags = stream.get("tags")
        tags: dict[str, Any] = raw_tags if isinstance(raw_tags, dict) else {}
        title = str(tags.get("title", "")).strip()
        codec = str(stream.get("codec_name", "audio"))
        channels = stream.get("channels", "?")
        entries.append({"selector": selector, "label": f"{selector}  {title or f'{codec} / {channels}ch'}"})
    return entries


def _normalized_channel(channel: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
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
    project: dict[str, Any],
    video_tracks: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw_current = project.get("audio_mix")
    current: dict[str, Any] = raw_current if isinstance(raw_current, dict) else {}
    raw_existing_channels = current.get("channels")
    existing_channels: list[Any] = raw_existing_channels if isinstance(raw_existing_channels, list) else []
    existing_by_id = {
        str(channel.get("id")): channel
        for channel in existing_channels
        if isinstance(channel, dict) and channel.get("id")
    }
    existing_video = [channel for channel in existing_channels if isinstance(channel, dict) and channel.get("kind") == "video"]
    existing_external = [
        channel
        for channel in existing_channels
        if isinstance(channel, dict) and channel.get("kind") == "external"
    ]
    claimed_existing: set[int] = set()

    def existing_channel(*ids: str, kind: str, path: str = "") -> dict[str, Any]:
        for channel_id in ids:
            candidate = existing_by_id.get(channel_id)
            if isinstance(candidate, dict) and id(candidate) not in claimed_existing and candidate.get("kind") == kind:
                claimed_existing.add(id(candidate))
                return candidate
        if kind == "external" and path:
            for candidate in existing_external:
                if id(candidate) not in claimed_existing and str(candidate.get("path", "")) == path:
                    claimed_existing.add(id(candidate))
                    return candidate
        return {}

    supplied_tracks = None if video_tracks is None else list(video_tracks)
    preserve_external = (
        supplied_tracks is None
        or bool(existing_video)
        or (not supplied_tracks and bool(existing_external))
    )

    preferred_selector = str(
        project.get("render_settings", {}).get("output_audio_track") or DEFAULT_AUDIO_TRACK
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
    enabled_selector = preferred_selector if preferred_selector in selectors else (track_entries[0]["selector"] if track_entries else "")
    channels: list[dict[str, Any]] = []
    used_channel_ids: set[str] = set()
    for entry in track_entries:
        selector = entry["selector"]
        channel_id = _video_channel_id(selector)
        used_channel_ids.add(channel_id)
        defaults = {
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

    for index, source in enumerate(project.get("audio_sources", [])):
        if not isinstance(source, dict) or not str(source.get("path", "")).strip():
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

    audio_mix = {
        "version": AUDIO_MIX_VERSION,
        "customized": bool(current.get("customized", False)),
        "channels": channels,
    }
    project["audio_mix"] = audio_mix
    return audio_mix


def reset_audio_mix(
    project: dict[str, Any],
    video_tracks: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    project.pop("audio_mix", None)
    return reconcile_audio_mix(project, video_tracks)


def active_audio_mix_channels(audio_mix: dict[str, Any]) -> list[dict[str, Any]]:
    enabled = [
        deepcopy(channel)
        for channel in audio_mix.get("channels", [])
        if isinstance(channel, dict) and bool(channel.get("enabled")) and not bool(channel.get("muted"))
    ]
    solo = [channel for channel in enabled if bool(channel.get("solo"))]
    return solo or enabled


def build_audio_mix_filter(
    audio_mix: dict[str, Any],
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
    final_filter = (
        f"{post_filter},{AUDIO_MIX_MASTER_FILTER},apad"
        if post_filter
        else f"{AUDIO_MIX_MASTER_FILTER},apad"
    )
    filters.append(f"[{base_label}]{final_filter}[{output_label}]")
    return input_args, ";".join(filters)
