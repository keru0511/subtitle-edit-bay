from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.audio_preview_cache import (
    AudioPreviewCacheResult,
    audio_preview_cache_entries,
)
from src.gui_audio_preview_controller import AudioPreviewController


class AudioPreviewControllerTests(unittest.TestCase):
    def _project(self, root: Path) -> dict[str, Any]:
        video = root / "capture.mkv"
        external = root / "speaker.aac"
        video.write_bytes(b"video-source")
        external.write_bytes(b"external-source")
        return {
            "video": {"path": str(video)},
            "transcription": {"offset_seconds": 0.25},
            "audio_mix": {
                "channels": [
                    {
                        "id": "video:0:a:0",
                        "kind": "video",
                        "selector": "0:a:0",
                        "enabled": True,
                        "muted": False,
                        "solo": False,
                        "volume_percent": 100,
                    },
                    {
                        "id": "external:speaker",
                        "kind": "external",
                        "path": str(external),
                        "enabled": False,
                        "muted": False,
                        "solo": False,
                        "volume_percent": 100,
                    },
                ]
            },
        }

    @staticmethod
    def _write_cache(project: dict[str, Any], cache_root: Path) -> dict[str, str]:
        entries = audio_preview_cache_entries(project, cache_root)
        paths: dict[str, str] = {}
        for entry in entries:
            entry.output_path.parent.mkdir(parents=True, exist_ok=True)
            entry.output_path.write_bytes(b"cached-audio")
            paths[entry.channel_id] = str(entry.output_path)
        return paths

    def test_channel_view_and_gain_reuse_project_mixer_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            controller = AudioPreviewController(root / "cache")
            self.addCleanup(controller.shutdown)
            controller.set_project(project)
            controller.set_cache_paths(self._write_cache(project, root / "cache"))

            channels = controller.mixer_channels
            self.assertEqual(channels[1]["preview_offset_seconds"], 0.25)
            self.assertEqual(channels[0]["preview_audio_track_index"], 0)
            self.assertEqual(
                controller.preview_gains,
                {"video:0:a:0": 1.0},
            )
            self.assertEqual(
                [item["id"] for item in controller.preview_channels],
                ["video:0:a:0"],
            )

            project["audio_mix"]["channels"][1]["enabled"] = True
            controller.notify_preview(structure_changed=True)
            self.assertEqual(
                controller.preview_gains,
                {"video:0:a:0": 1.0, "external:speaker": 1.0},
            )

    def test_level_publish_coalesces_peak_and_fades_to_silence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = AudioPreviewController(Path(temporary) / "cache")
            self.addCleanup(controller.shutdown)
            controller.gains.update({"video:0:a:0": 1.0})

            controller.pending_levels["video:0:a:0"] = 1.0
            controller.publish_levels()
            self.assertEqual(controller.levels["video:0:a:0"], 1.0)

            controller.gains.clear()
            controller.publish_levels()
            self.assertEqual(controller.levels["video:0:a:0"], 0.68)

            controller.levels.clear()
            controller.pending_levels.clear()

    def test_cache_hit_miss_completion_and_clear_keep_generation_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            calls: list[Path] = []

            def prepare(
                _project: dict[str, Any],
                cache_root: Path,
                *,
                protected_paths: list[Path],
            ) -> AudioPreviewCacheResult:
                calls.append(cache_root)
                self.assertTrue(protected_paths)
                paths = self._write_cache(project, cache_root)
                return AudioPreviewCacheResult(paths)

            cleared: list[Path] = []
            controller = AudioPreviewController(
                root / "cache",
                prepare_cache=prepare,
                clear_cache=lambda cache_root: cleared.append(Path(cache_root)),
            )
            self.addCleanup(controller.shutdown)
            controller.set_project(project)
            controller.set_cache_paths(self._write_cache(project, root / "cache"))
            controller.prepare_preview()
            self.assertEqual(calls, [])

            first_entry = audio_preview_cache_entries(project, root / "cache")[0]
            first_entry.output_path.unlink()
            controller.prepare_preview()
            future = controller.cache_future
            self.assertIsNotNone(future)
            if future is not None:
                result = future.result(timeout=2)
                controller.apply_audio_preview_cache(controller.cache_request, result)
            self.assertFalse(controller.preparing)
            self.assertEqual(calls, [root / "cache"])
            self.assertTrue(controller.preview_complete)

            generation = controller.generation
            controller.clear_cache()
            self.assertEqual(cleared, [root / "cache"])
            self.assertEqual(controller.generation, generation + 1)
            self.assertEqual(controller.cache_paths, {})


if __name__ == "__main__":
    unittest.main()
