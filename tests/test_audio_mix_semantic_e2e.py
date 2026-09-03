from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.audio_mixer import reconcile_audio_mix
from src.subtitle_project import create_project, load_project, save_project
from tests.media_test_utils import (
    AudioFixture,
    AudioLevelMeasurement,
    IntegratedLoudnessMeasurement,
    MediaFixture,
    MediaSegment,
    create_lavfi_audio_fixture,
    create_lavfi_av_fixture,
    measure_audio_level,
    measure_integrated_loudness,
    require_media_tools,
    run_media_command,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DURATION_SECONDS = 6.0
FIXTURE_SAMPLE_RATE = 48_000
FIXTURE_CHANNEL_LAYOUT = "stereo"
FIXTURE_VOLUME_DB = -12.0
VIDEO_FREQUENCY_HZ = 440
EXTERNAL_A_FREQUENCY_HZ = 880
EXTERNAL_B_FREQUENCY_HZ = 1320
BANDWIDTH_HZ = 40
MINIMUM_DOMINANCE_DB = 15.0
GAIN_TOLERANCE_DB = 2.0
NORMALIZE_TARGET_LUFS = -18.0
NORMALIZE_TOLERANCE_LU = 1.5
UNNORMALIZED_TOLERANCE_LU = 1.0
LOUDNESS_WARM_UP_SECONDS = 1.0
LOUDNESS_MEASUREMENT_SECONDS = 4.0


def _channel_state(
    *,
    enabled: bool,
    muted: bool = False,
    solo: bool = False,
    volume_percent: float = 100.0,
) -> dict[str, bool | float]:
    return {
        "enabled": enabled,
        "muted": muted,
        "solo": solo,
        "volume_percent": volume_percent,
    }


@unittest.skipUnless(
    os.environ.get("RUN_FFMPEG_SMOKE") == "1",
    "set RUN_FFMPEG_SMOKE=1 to exercise semantic media E2E",
)
class AudioMixSemanticE2ETests(unittest.TestCase):
    _temporary: tempfile.TemporaryDirectory[str]
    root: Path
    video: MediaFixture
    external_a: AudioFixture
    external_b: AudioFixture

    @classmethod
    def setUpClass(cls) -> None:
        require_media_tools()
        cls._temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temporary.cleanup)
        cls.root = Path(cls._temporary.name)
        cls.video = create_lavfi_av_fixture(
            cls.root / "video 440 Hz.mp4",
            [
                MediaSegment(
                    "steady tone",
                    FIXTURE_DURATION_SECONDS,
                    "0x202020",
                    VIDEO_FREQUENCY_HZ,
                )
            ],
            fps=15,
            sample_rate=FIXTURE_SAMPLE_RATE,
            audio_channel_layout=FIXTURE_CHANNEL_LAYOUT,
            tone_volume_db=FIXTURE_VOLUME_DB,
        )
        cls.external_a = create_lavfi_audio_fixture(
            cls.root / "external A 880 Hz.wav",
            frequency_hz=EXTERNAL_A_FREQUENCY_HZ,
            duration_seconds=FIXTURE_DURATION_SECONDS,
            sample_rate=FIXTURE_SAMPLE_RATE,
            channel_layout=FIXTURE_CHANNEL_LAYOUT,
            volume_db=FIXTURE_VOLUME_DB,
        )
        cls.external_b = create_lavfi_audio_fixture(
            cls.root / "external B 1320 Hz.wav",
            frequency_hz=EXTERNAL_B_FREQUENCY_HZ,
            duration_seconds=FIXTURE_DURATION_SECONDS,
            sample_rate=FIXTURE_SAMPLE_RATE,
            channel_layout=FIXTURE_CHANNEL_LAYOUT,
            volume_db=FIXTURE_VOLUME_DB,
        )

    @classmethod
    def _channel_key(cls, channel: dict[str, Any]) -> str:
        if channel.get("kind") == "video":
            return "video"
        channel_path = Path(str(channel.get("path", "")))
        if channel_path == cls.external_a.path:
            return "external_a"
        if channel_path == cls.external_b.path:
            return "external_b"
        raise AssertionError(f"Unexpected semantic audio channel: {channel!r}")

    @classmethod
    def _render_case(
        cls,
        name: str,
        states: dict[str, dict[str, bool | float]],
        *,
        audio_normalize: bool = False,
        audio_target_lufs: float = NORMALIZE_TARGET_LUFS,
    ) -> tuple[Path, dict[str, Any]]:
        project = create_project(
            video_path=cls.video.path,
            output_dir=cls.root,
            segments=[],
            audio_sources=[
                {
                    "name": "external A 880 Hz",
                    "track_key": "semantic:external-a",
                    "file_name": cls.external_a.path.name,
                    "path": str(cls.external_a.path),
                },
                {
                    "name": "external B 1320 Hz",
                    "track_key": "semantic:external-b",
                    "file_name": cls.external_b.path.name,
                    "path": str(cls.external_b.path),
                },
            ],
            duration_seconds=FIXTURE_DURATION_SECONDS,
        )
        audio_mix = reconcile_audio_mix(
            project,
            [{"selector": "0:a:0", "label": "video 440 Hz"}],
        )
        audio_mix["customized"] = True
        for channel in audio_mix["channels"]:
            key = cls._channel_key(channel)
            channel.update(states[key])

        project_path = cls.root / f"{name}.subtitle-project.json"
        output_path = cls.root / f"{name}.mp4"
        runtime_config_path = cls.root / f"{name}.runtime.json"
        save_project(project_path, project)
        runtime_config_path.write_text(
            json.dumps(
                {
                    "craig_pipeline": {
                        "video_codec": "libx264",
                        "audio_codec": "aac",
                        "x264_crf": 32,
                        "audio_normalize": audio_normalize,
                        "audio_target_lufs": audio_target_lufs,
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        run_media_command(
            [
                sys.executable,
                "-m",
                "src.subtitle_workflow",
                "render",
                "--project",
                str(project_path),
                "--output",
                str(output_path),
                "--config",
                str(runtime_config_path),
                "--run",
            ],
            cwd=REPO_ROOT,
            context=(
                f"production project audio mix render: case={name}, normalize={audio_normalize}, "
                f"target={audio_target_lufs:g} LUFS, video={cls.video.describe()}, "
                f"external_a={cls.external_a.describe()}, external_b={cls.external_b.describe()}"
            ),
        )
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise AssertionError(
                f"Production audio mix render did not create the expected output: expected={output_path}"
            )
        saved_project = load_project(project_path)
        cls._assert_saved_state(
            saved_project,
            states,
            audio_normalize=audio_normalize,
            audio_target_lufs=audio_target_lufs,
        )
        saved_output = Path(str(saved_project["render_settings"].get("last_output", "")))
        if saved_output != output_path.resolve():
            raise AssertionError(
                f"Rendered project points to a different final output: saved={saved_output}, expected={output_path.resolve()}"
            )
        return output_path, saved_project

    @classmethod
    def _assert_saved_state(
        cls,
        project: dict[str, Any],
        expected_states: dict[str, dict[str, bool | float]],
        *,
        audio_normalize: bool,
        audio_target_lufs: float,
    ) -> None:
        audio_mix = project["audio_mix"]
        if not audio_mix.get("customized"):
            raise AssertionError(f"Rendered project lost customized audio mix state: {audio_mix!r}")
        actual_states = {
            cls._channel_key(channel): {
                field: channel[field] for field in ("enabled", "muted", "solo", "volume_percent")
            }
            for channel in audio_mix["channels"]
        }
        if actual_states != expected_states:
            raise AssertionError(
                "Rendered project audio state differs from the requested mix.\n"
                f"expected={expected_states!r}\nactual={actual_states!r}"
            )
        render_settings = project["render_settings"]
        if render_settings.get("audio_normalize") is not audio_normalize:
            raise AssertionError(
                f"Saved audio_normalize differs: expected={audio_normalize}, settings={render_settings!r}"
            )
        if not math.isclose(
            float(render_settings.get("audio_target_lufs")),
            audio_target_lufs,
            abs_tol=0.001,
        ):
            raise AssertionError(
                f"Saved audio target differs: expected={audio_target_lufs} LUFS, settings={render_settings!r}"
            )

    @staticmethod
    def _measure_bands(path: Path) -> dict[int, AudioLevelMeasurement]:
        return {
            frequency_hz: measure_audio_level(
                path,
                frequency_hz=frequency_hz,
                bandwidth_hz=BANDWIDTH_HZ,
            )
            for frequency_hz in (
                VIDEO_FREQUENCY_HZ,
                EXTERNAL_A_FREQUENCY_HZ,
                EXTERNAL_B_FREQUENCY_HZ,
            )
        }

    @staticmethod
    def _level_report(*measurements: AudioLevelMeasurement) -> str:
        return "frequency levels and FFmpeg diagnostics:\n" + "\n\n".join(
            measurement.describe() for measurement in measurements
        )

    @staticmethod
    def _loudness_report(*measurements: IntegratedLoudnessMeasurement) -> str:
        return "integrated loudness and FFmpeg diagnostics:\n" + "\n\n".join(
            measurement.describe() for measurement in measurements
        )

    @staticmethod
    def _measure_loudness(path: Path) -> IntegratedLoudnessMeasurement:
        return measure_integrated_loudness(
            path,
            start_seconds=LOUDNESS_WARM_UP_SECONDS,
            duration_seconds=LOUDNESS_MEASUREMENT_SECONDS,
        )

    def test_mute_suppresses_only_the_muted_frequency(self) -> None:
        states = {
            "video": _channel_state(enabled=True, muted=True),
            "external_a": _channel_state(enabled=True),
            "external_b": _channel_state(enabled=True),
        }
        output, _saved_project = self._render_case("mute-video", states)
        levels = self._measure_bands(output)
        source_video = measure_audio_level(
            self.video.path,
            frequency_hz=VIDEO_FREQUENCY_HZ,
            bandwidth_hz=BANDWIDTH_HZ,
        )
        muted = levels[VIDEO_FREQUENCY_HZ]
        active = [levels[EXTERNAL_A_FREQUENCY_HZ], levels[EXTERNAL_B_FREQUENCY_HZ]]
        report = self._level_report(source_video, muted, *active)

        self.assertGreater(
            source_video.mean_volume_db,
            muted.mean_volume_db + MINIMUM_DOMINANCE_DB,
            f"Muted output must suppress the source 440 Hz by {MINIMUM_DOMINANCE_DB:g} dB.\n{report}",
        )
        self.assertGreater(
            min(measurement.mean_volume_db for measurement in active),
            -55.0,
            report,
        )
        self.assertGreater(
            min(measurement.mean_volume_db for measurement in active),
            muted.mean_volume_db + MINIMUM_DOMINANCE_DB,
            f"Muted 440 Hz must be at least {MINIMUM_DOMINANCE_DB:g} dB below both active frequencies.\n{report}",
        )

    def test_solo_frequency_dominates_every_non_solo_channel(self) -> None:
        states = {
            "video": _channel_state(enabled=True),
            "external_a": _channel_state(enabled=True),
            "external_b": _channel_state(enabled=True, solo=True),
        }
        output, _saved_project = self._render_case("solo-external-b", states)
        levels = self._measure_bands(output)
        solo = levels[EXTERNAL_B_FREQUENCY_HZ]
        non_solo = [levels[VIDEO_FREQUENCY_HZ], levels[EXTERNAL_A_FREQUENCY_HZ]]
        report = self._level_report(solo, *non_solo)

        self.assertGreater(solo.mean_volume_db, -55.0, report)
        self.assertGreater(
            solo.mean_volume_db,
            max(measurement.mean_volume_db for measurement in non_solo) + MINIMUM_DOMINANCE_DB,
            f"Solo 1320 Hz must dominate every non-solo channel by {MINIMUM_DOMINANCE_DB:g} dB.\n{report}",
        )

    def test_gain_changes_frequency_level_by_the_expected_decibels(self) -> None:
        levels: dict[int, AudioLevelMeasurement] = {}
        for gain_percent in (50, 100, 200):
            states = {
                "video": _channel_state(enabled=False),
                "external_a": _channel_state(enabled=True, volume_percent=float(gain_percent)),
                "external_b": _channel_state(enabled=False),
            }
            output, _saved_project = self._render_case(f"gain-{gain_percent}", states)
            levels[gain_percent] = measure_audio_level(
                output,
                frequency_hz=EXTERNAL_A_FREQUENCY_HZ,
                bandwidth_hz=BANDWIDTH_HZ,
            )

        report = self._level_report(*(levels[gain] for gain in (50, 100, 200)))
        for gain_percent in (50, 200):
            expected_change_db = 20.0 * math.log10(gain_percent / 100.0)
            actual_change_db = levels[gain_percent].mean_volume_db - levels[100].mean_volume_db
            self.assertAlmostEqual(
                actual_change_db,
                expected_change_db,
                delta=GAIN_TOLERANCE_DB,
                msg=(
                    f"880 Hz gain={gain_percent}% produced {actual_change_db:.2f} dB relative to 100%; "
                    f"expected={expected_change_db:.2f} dB, tolerance={GAIN_TOLERANCE_DB:.2f} dB.\n{report}"
                ),
            )

        broadband_200 = measure_audio_level(levels[200].path)
        self.assertLess(
            broadband_200.max_volume_db,
            -10.0,
            "The low-level gain fixture must stay below the limiter/clipping range.\n"
            + self._level_report(broadband_200, levels[200]),
        )

    def test_normalize_reaches_the_configured_ebu_r128_target(self) -> None:
        states = {
            "video": _channel_state(enabled=False),
            "external_a": _channel_state(enabled=True),
            "external_b": _channel_state(enabled=False),
        }
        normalize_off, _off_project = self._render_case(
            "normalize-off",
            states,
            audio_normalize=False,
        )
        normalize_on, _on_project = self._render_case(
            "normalize-on",
            states,
            audio_normalize=True,
            audio_target_lufs=NORMALIZE_TARGET_LUFS,
        )
        input_loudness = self._measure_loudness(self.external_a.path)
        off_loudness = self._measure_loudness(normalize_off)
        on_loudness = self._measure_loudness(normalize_on)
        report = self._loudness_report(input_loudness, off_loudness, on_loudness)

        self.assertAlmostEqual(
            off_loudness.integrated_lufs,
            input_loudness.integrated_lufs,
            delta=UNNORMALIZED_TOLERANCE_LU,
            msg=(
                "normalize=off must preserve integrated loudness: "
                f"tolerance={UNNORMALIZED_TOLERANCE_LU:.2f} LU.\n{report}"
            ),
        )
        self.assertAlmostEqual(
            on_loudness.integrated_lufs,
            NORMALIZE_TARGET_LUFS,
            delta=NORMALIZE_TOLERANCE_LU,
            msg=(
                f"normalize=on target={NORMALIZE_TARGET_LUFS:.2f} LUFS, "
                f"tolerance={NORMALIZE_TOLERANCE_LU:.2f} LU.\n{report}"
            ),
        )
        self.assertLess(
            abs(on_loudness.integrated_lufs - NORMALIZE_TARGET_LUFS),
            abs(off_loudness.integrated_lufs - NORMALIZE_TARGET_LUFS),
            f"Normalization must move integrated loudness closer to target={NORMALIZE_TARGET_LUFS:.2f} LUFS.\n{report}",
        )


if __name__ == "__main__":
    unittest.main()
