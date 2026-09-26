from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.audio_mixer import reconcile_audio_mix
from src.subtitle_project import create_project, load_project, save_project
from tests.typed_case import TypedTestCase


class AudioMixerTests(TypedTestCase):
    def test_reconcile_preserves_external_controls_without_video_tracks_after_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video-only.mkv"
            first_audio = root / "1-alice.flac"
            second_audio = root / "2-bob.flac"
            video.write_bytes(b"video")
            first_audio.write_bytes(b"audio")
            second_audio.write_bytes(b"audio")
            project = create_project(
                video_path=video,
                output_dir=root,
                audio_sources=[
                    {"path": str(first_audio), "track_key": "craig:alice"},
                    {"path": str(second_audio), "track_key": "craig:bob"},
                ],
                segments=[],
                duration_seconds=1.0,
            )
            reconcile_audio_mix(project, video_tracks=[])
            first, second = project["audio_mix"]["channels"]
            first.update({"enabled": True, "muted": True, "solo": False, "volume_percent": 42})
            second.update({"enabled": True, "muted": False, "solo": True, "volume_percent": 157})
            project_path = root / "video-only.subtitle-project.json"
            save_project(project_path, project)
            reloaded = load_project(project_path)
            reconciled = reconcile_audio_mix(reloaded, video_tracks=[])

        self.assertEqual(
            [
                (
                    channel["volume_percent"],
                    channel["muted"],
                    channel["solo"],
                    channel["enabled"],
                )
                for channel in reconciled["channels"]
            ],
            [(42.0, True, False, True), (157.0, False, True, True)],
        )


if __name__ == "__main__":
    unittest.main()
