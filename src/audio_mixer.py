from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


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


def _clamp_volume(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 100.0
    return max(0.0, min(MAX_VOLUME_PERCENT, numeric))


def _video_channel_id(selector: str) -> str:
    return f"video:{selector}"


def _external_channel_id(source: dict[str, Any], index: int) -> str:
    identity = str(source.get("track_key") or source.get("path") or source.get("file_name") or index)
    return f"external:{identity}"


def video_track_entries(streams: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for audio_index, stream in enumerate(streams):
        selector = f"0:a:{audio_index}"
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        title = str(tags.get("title", "")).strip()
        codec = str(stream.get("codec_name", "audio"))
        channels = stream.get("channels", "?")
        entries.append({"selector": selector, "label": f"{selector}  {title or f'{codec} / {channels}ch'}"})
    return entries


def _normalized_channel(channel: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    normalized = {**defaults, **deepcopy(channel)}
    normalized["id"] = str(defaults["id"])
    normalized["kind"] = str(defaults["kind"])
    normalized["label"] = str(normalized.get("label") or defaults["label"])
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
    current = project.get("audio_mix") if isinstance(project.get("audio_mix"), dict) else {}
    existing_channels = current.get("channels") if isinstance(current.get("channels"), list) else []
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
    preserve_external = video_tracks is None or bool(existing_video) or bool(existing_external)

    preferred_selector = str(
        project.get("render_settings", {}).get("output_audio_track") or DEFAULT_AUDIO_TRACK
    )
    if video_tracks is None:
        track_entries = [
            {"selector": str(channel.get("selector", "")), "label": str(channel.get("label", ""))}
            for channel in existing_video
            if str(channel.get("selector", "")).strip()
        ]
        if not track_entries and not project.get("audio_sources"):
            track_entries = [{"selector": preferred_selector, "label": preferred_selector}]
    else:
        supplied_tracks = list(video_tracks)
        track_entries = [
            {"selector": str(track.get("selector", "")), "label": str(track.get("label", ""))}
            for track in supplied_tracks
            if str(track.get("selector", "")).strip()
        ]

    selectors = {entry["selector"] for entry in track_entries}
    enabled_selector = preferred_selector if preferred_selector in selectors else (track_entries[0]["selector"] if track_entries else "")
    channels: list[dict[str, Any]] = []
    for entry in track_entries:
        selector = entry["selector"]
        defaults = {
            "id": _video_channel_id(selector),
            "kind": "video",
            "label": entry["label"] or selector,
            "selector": selector,
            "enabled": selector == enabled_selector,
            "muted": False,
            "solo": False,
            "volume_percent": 100.0,
        }
        channels.append(_normalized_channel(existing_by_id.get(defaults["id"], {}), defaults))

    for index, source in enumerate(project.get("audio_sources", [])):
        if not isinstance(source, dict) or not str(source.get("path", "")).strip():
            continue
        channel_id = _external_channel_id(source, index)
        speaker_name = str(source.get("name") or source.get("file_name") or Path(str(source["path"])).name)
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
        existing_channel = existing_by_id.get(channel_id, {})
        if existing_channel and not preserve_external:
            existing_channel = {}
        channels.append(_normalized_channel(existing_channel, defaults))

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
