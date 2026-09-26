from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.burn_subs import run_ffmpeg_burn
from src.silence_cut import cut_media_ranges
from src.subtitle_project import create_project, save_project
from src.subtitle_workflow_transcription import _extract_video_audio_track
from src.transcribe import probe_audio_streams
from tests.media_test_helpers import (
    MediaSegment,
    assert_mp4_faststart,
    create_lavfi_audio_fixture,
    create_lavfi_av_fixture,
    media_duration_seconds,
    probe_media,
    video_stream,
)
from tests.typed_case import TypedTestCase


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


class RuntimeMediaSmokeTests(TypedTestCase):
    def _require_ffmpeg(self) -> None:
        if not (_has_tool("ffmpeg") and _has_tool("ffprobe")):
            self.skipTest("ffmpeg and ffprobe are required for runtime media smoke tests")

    def _make_video(self, path: Path, pix_fmt: str = "yuv420p", duration: float = 1.0) -> Path:
        self._require_ffmpeg()
        create_lavfi_av_fixture(
            path,
            [MediaSegment("testsrc", duration, "testsrc", 440)],
            fps=15,
            video_source="testsrc",
            video_pix_fmt=pix_fmt,
        )
        return path

    def _make_wav(self, path: Path, duration: float = 1.0) -> Path:
        self._require_ffmpeg()
        create_lavfi_audio_fixture(
            path,
            frequency_hz=880,
            duration_seconds=duration,
            sample_rate=48_000,
            channel_layout="mono",
        )
        return path

    def _probe_video_stream(self, path: Path) -> dict:
        self._require_ffmpeg()
        return video_stream(probe_media(path))

    def _assert_faststart_moov_before_mdat(self, output: Path) -> None:
        assert_mp4_faststart(output)

    def test_long_cut_media_ranges_preserves_video_audio_and_resolution(self) -> None:
        if os.environ.get("RUN_FFMPEG_SMOKE") != "1":
            self.skipTest("set RUN_FFMPEG_SMOKE=1 to exercise FFmpeg media processing")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = self._make_video(root / "input.mp4", duration=6.4)
            output = root / "cut.mp4"
            keep_ranges = [
                (index * 0.1, index * 0.1 + 0.08)
                for index in range(64)
            ]

            cut_media_ranges(str(video), str(output), keep_ranges)

            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            self.assertGreaterEqual(len(probe_audio_streams(str(output))), 1)
            stream = self._probe_video_stream(output)
            self.assertEqual(stream["width"], 320)
            self.assertEqual(stream["height"], 180)
            self.assertEqual(stream["pix_fmt"], "yuv420p")
            # The keep ranges total 64 * 0.08s. A 30 fps encode and MP4 muxing
            # can round the boundary by a few frames, so use a bounded 0.25s
            # tolerance rather than treating container duration as byte-exact.
            output_duration = media_duration_seconds(probe_media(output))
            expected_duration = sum(end - start for start, end in keep_ranges)
            self.assertAlmostEqual(output_duration, expected_duration, delta=0.25)
            self._assert_faststart_moov_before_mdat(output)

    def test_burn_subtitles_converts_10bit_and_444_inputs_to_yuv420p(self) -> None:
        if os.environ.get("RUN_FFMPEG_SMOKE") != "1":
            self.skipTest("set RUN_FFMPEG_SMOKE=1 to exercise FFmpeg media processing")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subtitle = root / "caption.ass"
            subtitle.write_text(
                "\n".join([
                    "[Script Info]",
                    "ScriptType: v4.00+",
                    "PlayResX: 320",
                    "PlayResY: 180",
                    "",
                    "[V4+ Styles]",
                    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                    "Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1",
                    "",
                    "[Events]",
                    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
                    "Dialogue: 0,0:00:00.00,0:00:00.80,Default,,0,0,0,,smoke subtitle",
                    "",
                ]),
                encoding="utf-8",
            )

            for pix_fmt in ["yuv420p10le", "yuv444p"]:
                video = self._make_video(root / f"input-{pix_fmt}.mp4", pix_fmt=pix_fmt)
                output = root / f"burned-{pix_fmt}.mp4"
                result = run_ffmpeg_burn(
                    str(video),
                    str(subtitle),
                    str(output),
                    video_codec="libx264",
                    audio_codec="aac",
                )

                self.assertEqual(result, output)
                stream = self._probe_video_stream(output)
                self.assertEqual(stream["codec_name"], "h264")
                self.assertEqual(stream.get("profile"), "High")
                self.assertEqual(stream["pix_fmt"], "yuv420p")

    def test_extract_video_audio_track_uses_distinct_cache_for_same_stem(self) -> None:
        if os.environ.get("RUN_FFMPEG_SMOKE") != "1":
            self.skipTest("set RUN_FFMPEG_SMOKE=1 to exercise FFmpeg media processing")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dir1 = root / "dir1"
            dir2 = root / "dir2"
            dir1.mkdir()
            dir2.mkdir()
            video1 = self._make_video(dir1 / "game.mp4", duration=1.0)
            video2 = self._make_video(dir2 / "game.mp4", duration=1.2)
            transcript_dir = root / "transcripts"

            path1 = _extract_video_audio_track(str(video1), "0:a:0", transcript_dir)
            path2 = _extract_video_audio_track(str(video2), "0:a:0", transcript_dir)

            self.assertNotEqual(path1, path2)
            self.assertTrue(path1.exists())
            self.assertTrue(path2.exists())
            self.assertEqual(path1, _extract_video_audio_track(str(video1), "0:a:0", transcript_dir))

    def test_qt_multimedia_can_play_generated_video(self) -> None:
        if os.environ.get("RUN_QT_MEDIA_SMOKE") != "1":
            self.skipTest("set RUN_QT_MEDIA_SMOKE=1 to exercise Qt Multimedia playback")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            video = self._make_video(Path(temp_dir) / "playback.mp4", duration=1.2)

            from PySide6.QtCore import QTimer, QUrl
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication(["subtitle-edit-bay-media-smoke"])
            player = QMediaPlayer()
            audio = QAudioOutput()
            audio.setMuted(True)
            sink = QVideoSink()
            state = {"advanced": False, "frame": False, "errors": []}

            player.setAudioOutput(audio)
            player.setVideoSink(sink)
            player.positionChanged.connect(lambda position: state.__setitem__("advanced", state["advanced"] or position > 0))
            sink.videoFrameChanged.connect(lambda _frame: state.__setitem__("frame", True))
            player.errorOccurred.connect(lambda _error, message: state["errors"].append(message or "media playback error"))
            # Qt Multimedia may need several seconds to initialize its bundled
            # FFmpeg backend on a newly provisioned Windows hosted runner.
            # Exit as soon as playback is proven, while retaining a bounded
            # timeout that still catches a decoder which never starts.
            player.positionChanged.connect(lambda position: app.quit() if position > 0 else None)
            sink.videoFrameChanged.connect(lambda _frame: app.quit())
            QTimer.singleShot(10000, app.quit)
            player.setSource(QUrl.fromLocalFile(str(video)))
            player.play()
            app.exec()
            player.stop()
            player.setSource(QUrl())
            player.setVideoSink(None)
            player.setAudioOutput(None)
            app.processEvents()

            self.assertEqual(state["errors"], [])
            self.assertTrue(state["advanced"] or state["frame"])

    def test_gui_backend_audio_mixer_operations_update_project_state(self) -> None:
        if os.environ.get("RUN_GUI_AUDIO_MIXER_SMOKE") != "1":
            self.skipTest("set RUN_GUI_AUDIO_MIXER_SMOKE=1 to exercise GUI mixer operations")

        from PySide6.QtWidgets import QApplication

        from src.gui import EditBayBackend

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = self._make_video(root / "session.mp4")
            speaker_audio = self._make_wav(root / "1-speaker-a.wav")
            project = create_project(
                video_path=video,
                output_dir=root,
                duration_seconds=1.0,
                segments=[{"start": 0.0, "end": 0.8, "text": "hello", "speaker": "Oz"}],
                audio_sources=[
                    {
                        "name": "speaker-a",
                        "style": "Oz",
                        "track_key": "craig:speaker-a",
                        "file_name": speaker_audio.name,
                        "path": str(speaker_audio),
                        "color": "#FFD966",
                    }
                ],
                speakers=[
                    {
                        "name": "speaker-a",
                        "style": "Oz",
                        "track_key": "craig:speaker-a",
                        "file_name": speaker_audio.name,
                        "path": str(speaker_audio),
                        "color": "#FFD966",
                    }
                ],
                waveforms=[
                    {
                        "speaker": "speaker-a",
                        "style": "Oz",
                        "color": "#FFD966",
                        "source_path": str(speaker_audio),
                        "offset_seconds": 0.0,
                        "duration_seconds": 1.0,
                        "sample_rate": 400,
                        "peaks": [0.2, 0.4, 0.3],
                    }
                ],
                transcription={"offset_seconds": 0.0},
            )
            project_path = save_project(root / "session.subtitle-project.json", project)

            app = QApplication.instance() or EditBayBackend(["subtitle-edit-bay-mixer-smoke"], workspace_root=root)
            backend = app if isinstance(app, EditBayBackend) else EditBayBackend(["subtitle-edit-bay-mixer-smoke"], workspace_root=root)
            backend.loadProject(str(project_path))

            channels = backend.audioMixerChannels
            self.assertGreaterEqual(len(channels), 2)
            external_index = next(index for index, channel in enumerate(channels) if channel.get("kind") == "external")
            backend.updateAudioMixChannel(external_index, {"enabled": True, "volume_percent": 125.0, "solo": True})

            updated = backend.audioMixerChannels[external_index]
            backend._audio_preview_cache_paths[str(updated["id"])] = str(speaker_audio)
            self.assertTrue(updated["enabled"])
            self.assertTrue(updated["solo"])
            self.assertEqual(updated["volume_percent"], 125.0)
            self.assertEqual(backend.audioMixerPreviewGains.get(str(updated["id"])), 1.25)
            backend.stopAudioMixerPreview()
            backend.quit()


if __name__ == "__main__":
    unittest.main()
