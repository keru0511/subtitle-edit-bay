"""動画書き出しの実行用設定が保存済みの編集状態を変えないことを確認する。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.subtitle_project import create_project, load_project, load_project_model, save_project
from src.subtitle_project_schema import AudioMix
from src.subtitle_workflow import render_project_video


class SubtitleRenderPlanningTests(unittest.TestCase):
    def test_uncut_render_clears_previous_cut_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[],
                render_settings={"last_cut_output": str(root / "old-cut.mp4")},
            )
            project_path = save_project(root / "project.subtitle-project.json", project)

            with patch("src.subtitle_workflow.run_ffmpeg_burn"):
                render_project_video(project_path, audio_normalize=False)

            render_settings = load_project_model(project_path).render_settings or {}
            self.assertNotIn("last_cut_output", render_settings)

    def test_audio_fallback_does_not_persist_an_automatic_channel_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mkv"
            video.write_bytes(b"video")
            audio = root / "voice.wav"
            audio.write_bytes(b"audio")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[],
                audio_sources=[{"path": str(audio)}],
                audio_mix={
                    "customized": False,
                    "channels": [
                        {"id": "video:0:a:0", "kind": "video", "selector": "0:a:0", "enabled": True},
                        {"id": "external:voice", "kind": "external", "path": str(audio), "enabled": False},
                    ],
                },
            )
            project_path = save_project(root / "project.subtitle-project.json", project)
            before = load_project(project_path)["audio_mix"]
            no_audio_streams: list[dict[str, object]] = []

            effective_audio_mixes: list[AudioMix] = []

            def capture_burn(_video: str, _subtitle: str | None, output: str, **kwargs: object) -> Path:
                effective_audio_mixes.append(AudioMix.from_json(kwargs.get("audio_mix")))
                return Path(output)

            with (
                patch("src.subtitle_workflow.probe_audio_streams", return_value=no_audio_streams),
                patch("src.subtitle_workflow.run_ffmpeg_burn", side_effect=capture_burn),
            ):
                render_project_video(project_path, audio_normalize=False)

            self.assertEqual(len(effective_audio_mixes), 1)
            self.assertTrue(
                next(channel.enabled for channel in effective_audio_mixes[0].channels if channel.kind == "external")
            )
            self.assertEqual(load_project(project_path)["audio_mix"], before)


if __name__ == "__main__":
    unittest.main()
