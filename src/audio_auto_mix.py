from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class ChannelGainSuggestion:
    channel_id: str
    gain_db: float
    source_level_db: float
    target_level_db: float
    reason: str
    excluded: bool = False


@dataclass(frozen=True)
class DuckingPoint:
    timestamp: float
    gain_db: float


def estimate_level_db(samples: Sequence[float], *, minimum_samples: int = 8) -> float | None:
    values = [abs(float(sample)) for sample in samples if math.isfinite(float(sample))]
    if len(values) < minimum_samples:
        return None
    values.sort()
    trim = max(1, int(len(values) * 0.05))
    stable = values[trim:-trim] if len(values) > trim * 2 else values
    rms = math.sqrt(sum(value * value for value in stable) / max(1, len(stable)))
    if rms <= 1e-9:
        return -120.0
    return 20.0 * math.log10(rms)


def suggest_channel_gains(
    channel_samples: Mapping[str, Sequence[float]],
    *,
    target_level_db: float = -18.0,
    minimum_gain_db: float = -6.0,
    maximum_gain_db: float = 6.0,
    excluded_channels: Iterable[str] = (),
) -> list[ChannelGainSuggestion]:
    excluded = {str(item) for item in excluded_channels}
    levels = {
        str(channel_id): estimate_level_db(samples)
        for channel_id, samples in channel_samples.items()
    }
    usable = [level for channel_id, level in levels.items() if level is not None and channel_id not in excluded and level > -119]
    baseline = statistics.median(usable) if usable else target_level_db
    result: list[ChannelGainSuggestion] = []
    for channel_id, level in levels.items():
        if channel_id in excluded:
            result.append(ChannelGainSuggestion(channel_id, 0.0, level or -120.0, target_level_db, "手動除外", True))
            continue
        if level is None:
            result.append(ChannelGainSuggestion(channel_id, 0.0, -120.0, target_level_db, "サンプル不足のため変更なし"))
            continue
        gain = max(minimum_gain_db, min(maximum_gain_db, baseline - level))
        result.append(ChannelGainSuggestion(channel_id, round(gain, 2), round(level, 2), round(target_level_db, 2), f"中央値{baseline:.1f}dBを基準に調整"))
    return result


def build_ducking_envelope(
    speech_intervals: Iterable[tuple[float, float]],
    *,
    total_duration: float,
    duck_amount_db: float = -9.0,
    attack_seconds: float = 0.05,
    release_seconds: float = 0.25,
    hold_seconds: float = 0.1,
) -> list[DuckingPoint]:
    amount = min(0.0, float(duck_amount_db))
    attack = max(0.0, float(attack_seconds))
    release = max(0.0, float(release_seconds))
    hold = max(0.0, float(hold_seconds))
    points = [DuckingPoint(0.0, 0.0)]
    for start, end in sorted(speech_intervals):
        start = max(0.0, min(float(total_duration), start))
        end = max(start, min(float(total_duration), end))
        if end <= start:
            continue
        points.extend(
            [
                DuckingPoint(max(0.0, start - attack), 0.0),
                DuckingPoint(start, amount),
                DuckingPoint(min(float(total_duration), end + hold), amount),
                DuckingPoint(min(float(total_duration), end + hold + release), 0.0),
            ]
        )
    return _dedupe_points(points)


def build_bgm_ducking_filter(
    *,
    bgm_label: str = "bgm",
    speech_label: str = "speech_bus",
    output_label: str = "bgm_ducked",
    duck_amount_db: float = -9.0,
    attack_seconds: float = 0.05,
    release_seconds: float = 0.25,
) -> str:
    amount = min(0.0, float(duck_amount_db))
    ratio = max(1.0, min(20.0, 1.0 + abs(amount) / 2.0))
    threshold = max(0.001, min(1.0, 10 ** (amount / 20.0)))
    return (
        f"[{bgm_label}][{speech_label}]sidechaincompress="
        f"threshold={threshold:.4f}:ratio={ratio:.2f}:"
        f"attack={max(0.0, attack_seconds) * 1000:.1f}:"
        f"release={max(0.0, release_seconds) * 1000:.1f}:"
        f"makeup=1[{output_label}]"
    )


def predict_limiter_reduction(
    peak_levels_db: Mapping[str, float],
    gains: Mapping[str, float],
    *,
    ceiling_db: float = -1.0,
) -> float:
    combined_peak = max(
        (float(peak_levels_db.get(channel_id, -120.0)) + float(gains.get(channel_id, 0.0)) for channel_id in peak_levels_db),
        default=-120.0,
    )
    return max(0.0, combined_peak - float(ceiling_db))


def _dedupe_points(points: Iterable[DuckingPoint]) -> list[DuckingPoint]:
    by_time: dict[float, DuckingPoint] = {}
    for point in points:
        if point.timestamp not in by_time or point.gain_db < by_time[point.timestamp].gain_db:
            by_time[point.timestamp] = point
    return [by_time[key] for key in sorted(by_time)]

