from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.audio_preview_cache import (
    audio_preview_cache_entries,
    cached_audio_preview_paths,
    prepare_audio_preview_cache,
)


class AudioPreviewCacheTests(unittest.TestCase):
    def _project(self, root: Path) -> dict[str, object]:
        video = root / "capture.mkv"
        video.write_bytes(b"video-source")
        external = root / "speaker.aac"
        external.write_bytes(b"external-source")
        return {
            "video": {"path": str(video)},
            "audio_mix": {
                "channels": [
                    {
                        "id": "video:0:a:0",
                        "kind": "video",
                        "selector": "0:a:0",
                    },
                    {
                        "id": "video:0:a:1",
                        "kind": "video",
                        "selector": "0:a:1",
                    },
                    {
                        "id": "external:speaker",
                        "kind": "external",
                        "path": str(external),
                    },
                ]
            },
        }

    def test_entries_are_per_track_and_change_with_source_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            cache_root = root / "cache"

            entries = audio_preview_cache_entries(project, cache_root)

            self.assertEqual(
                [entry.selector for entry in entries],
                ["0:a:0", "0:a:1", "0:a:0"],
            )
            self.assertEqual(len({entry.output_path for entry in entries}), 3)
            self.assertTrue(all(entry.output_path.suffix == ".mka" for entry in entries))

            original_path = entries[0].output_path
            video = Path(str(project["video"]["path"]))
            video.write_bytes(b"video-source-changed")
            changed = audio_preview_cache_entries(project, cache_root)
            self.assertNotEqual(changed[0].output_path, original_path)

    def test_prepare_groups_video_tracks_and_reuses_completed_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            calls: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                for value in command:
                    if str(value).endswith(".tmp.mka"):
                        Path(value).write_bytes(b"cached-audio")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("src.audio_preview_cache.subprocess.run", side_effect=fake_run):
                result = prepare_audio_preview_cache(project, root / "cache")

            self.assertEqual(result.errors, ())
            self.assertEqual(set(result.paths), {
                "video:0:a:0",
                "video:0:a:1",
                "external:speaker",
            })
            self.assertTrue(all(Path(path).is_file() for path in result.paths.values()))
            self.assertEqual(len(calls), 2)
            video_command = next(
                command for command in calls if str(project["video"]["path"]) in command
            )
            self.assertIn("0:a:0", video_command)
            self.assertIn("0:a:1", video_command)
            self.assertIn("+genpts", video_command)
            self.assertIn("flac", video_command)
            self.assertIn("48000", video_command)
            self.assertIn("-ac", video_command)
            self.assertNotIn("copy", video_command)
            self.assertTrue(all(path.endswith(".mka") for path in result.paths.values()))

            with patch("src.audio_preview_cache.subprocess.run") as run_again:
                reused = prepare_audio_preview_cache(project, root / "cache")

            run_again.assert_not_called()
            self.assertEqual(reused.paths, result.paths)

    def test_failed_source_does_not_publish_partial_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            error = subprocess.CalledProcessError(
                1,
                ["ffmpeg"],
                stderr="invalid audio stream",
            )

            with patch("src.audio_preview_cache.subprocess.run", side_effect=error):
                result = prepare_audio_preview_cache(project, root / "cache")

            self.assertEqual(result.paths, {})
            self.assertTrue(result.errors)
            entries = audio_preview_cache_entries(project, root / "cache")
            self.assertEqual(cached_audio_preview_paths(entries), {})
            self.assertEqual(list((root / "cache").glob("*.tmp.mka")), [])


if __name__ == "__main__":
    unittest.main()

