from __future__ import annotations

import subprocess
import tempfile
import unittest
import os
import time
from pathlib import Path
from unittest.mock import patch

from src.audio_preview_cache import (
    MAX_CACHE_AGE_SECONDS,
    MAX_CACHE_SIZE_BYTES,
    audio_preview_cache_entries,
    audio_preview_cache_stats,
    clear_audio_preview_cache,
    cached_audio_preview_paths,
    prune_audio_preview_cache,
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
                command for command in calls if "0:a:1" in command
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

    def test_prune_audio_preview_cache_removes_old_files_and_obeys_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            cache_root.mkdir()
            now = time.time()

            old_path = cache_root / "old.mka"
            recent_path = cache_root / "recent.mka"
            old_path.write_bytes(b"\0" * 4096)
            recent_path.write_bytes(b"\0" * 4096)
            os.utime(
                old_path,
                (now - (MAX_CACHE_AGE_SECONDS + 10), now - (MAX_CACHE_AGE_SECONDS + 10)),
            )
            os.utime(
                recent_path,
                (now - 1, now - 1),
            )

            stats = prune_audio_preview_cache(
                cache_root,
                max_bytes=MAX_CACHE_SIZE_BYTES,
                max_age_seconds=MAX_CACHE_AGE_SECONDS,
            )
            self.assertEqual(stats.removed_files, 1)
            self.assertFalse(old_path.is_file())
            self.assertTrue(recent_path.is_file())
            self.assertLessEqual(stats.total_bytes, MAX_CACHE_SIZE_BYTES)

    def test_prune_audio_preview_cache_with_zero_capacity_keeps_protected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            cache_root.mkdir()
            now = time.time()
            protected = cache_root / "keep.mka"
            removed_a = cache_root / "a.mka"
            removed_b = cache_root / "b.mka"
            removed_c = cache_root / "c.mka"
            protected.write_bytes(b"\0" * 1024)
            removed_a.write_bytes(b"\0" * 1024)
            removed_b.write_bytes(b"\0" * 1024)
            removed_c.write_bytes(b"\0" * 1024)
            os.utime(protected, (now - 100, now - 100))
            os.utime(removed_a, (now - 200, now - 200))
            os.utime(removed_b, (now - 300, now - 300))
            os.utime(removed_c, (now - 400, now - 400))

            stats = prune_audio_preview_cache(
                cache_root,
                max_bytes=0,
                max_age_seconds=MAX_CACHE_AGE_SECONDS,
                protected_paths={str(protected.resolve())},
            )
            self.assertEqual(stats.removed_files, 3)
            self.assertEqual(stats.removed_bytes, 3072)
            self.assertTrue(protected.is_file())
            self.assertFalse(removed_a.is_file())
            self.assertFalse(removed_b.is_file())
            self.assertFalse(removed_c.is_file())

    def test_clear_audio_preview_cache_removes_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            cache_root.mkdir()
            first = cache_root / "1.mka"
            second = cache_root / "2.mka"
            first.write_bytes(b"a")
            second.write_bytes(b"b")

            stats = clear_audio_preview_cache(cache_root)
            self.assertEqual(stats.removed_files, 2)
            self.assertEqual(stats.removed_bytes, 2)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())


if __name__ == "__main__":
    unittest.main()

