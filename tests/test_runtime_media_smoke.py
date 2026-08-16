from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.burn_subs import run_ffmpeg_burn
from src.subtitle_project import create_project, save_project
from src.transcribe import probe_audio_streams


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")


class RuntimeMediaSmokeTests(unittest.TestCase):
    def _require_ffmpeg(self) -> None:
        if not (_has_tool("ffmpeg") and _has_tool("ffprobe")):
            self.skipTest("ffmpeg and ffprobe are required for runtime media smoke tests")

    def _make_video(self, path: Path, duration: float = 1.0) -> Path:
        self._require_ffmpeg()
        _run([
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x180:rate=15:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ])
        return path

    def _write_ass(self, path: Path, text: str = "smoke subtitle") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
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
                f"Dialogue: 0,0:00:00.00,0:00:00.80,Default,,0,0,0,,{text}",
                "",
            ]),
            encoding="utf-8",
        )
        return path

    def _make_wav(self, path: Path, duration: float = 1.0) -> Path:
        self._require_ffmpeg()
        _run([
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:sample_rate=48000:duration={duration}",
            "-ac",
            "1",
            "-ar",
            "48000",
            str(path),
        ])
        return path

    def test_ffmpeg_burns_ass_subtitles_on_real_media(self) -> None:
        if os.environ.get("RUN_FFMPEG_SMOKE") != "1":
            self.skipTest("set RUN_FFMPEG_SMOKE=1 to exercise FFmpeg media processing")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = self._make_video(root / "input.mp4")
            subtitle = self._write_ass(root / "caption.ass")
            output = root / "burned.mp4"

            result = run_ffmpeg_burn(
                str(video),
                str(subtitle),
                str(output),
                video_codec="libx264",
                audio_codec="aac",
            )

            self.assertEqual(result, output)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            self.assertGreaterEqual(len(probe_audio_streams(str(output))), 1)

    def test_ffmpeg_export_handles_apostrophe_path_and_writes_faststart_mp4(self) -> None:
        if os.environ.get("RUN_FFMPEG_SMOKE") != "1":
            self.skipTest("set RUN_FFMPEG_SMOKE=1 to exercise FFmpeg media processing")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = self._make_video(root / "input.mp4")
            quoted = root / "O'Brien"
            subtitle = self._write_ass(quoted / "caption.ass")
            output = quoted / "finished.mp4"

            run_ffmpeg_burn(str(video), str(subtitle), str(output), video_codec="libx264")

            payload = output.read_bytes()
            self.assertGreater(len(payload), 0)
            self.assertGreaterEqual(payload.find(b"moov"), 0)
            self.assertLess(payload.find(b"moov"), payload.find(b"mdat"))

    def test_ffmpeg_export_converts_pcm_audio_and_high_bit_depth_video_for_mp4(self) -> None:
        if os.environ.get("RUN_FFMPEG_SMOKE") != "1":
            self.skipTest("set RUN_FFMPEG_SMOKE=1 to exercise FFmpeg media processing")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subtitle = self._write_ass(root / "caption.ass")
            for pixel_format, profile in (("yuv444p", "high444"), ("yuv420p10le", "high10")):
                with self.subTest(pixel_format=pixel_format):
                    source = root / f"source-{pixel_format}.mkv"
                    _run([
                        "ffmpeg", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
                        "-shortest",
                        "-c:v", "libx264", "-profile:v", profile, "-pix_fmt", pixel_format,
                        "-c:a", "pcm_s16le",
                        str(source),
                    ])
                    output = root / f"output-{pixel_format}.mp4"

                    run_ffmpeg_burn(str(source), str(subtitle), str(output), video_codec="libx264", audio_codec="copy")

                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
                        check=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    streams = json.loads(probe.stdout)["streams"]
                    video_stream = next(stream for stream in streams if stream["codec_type"] == "video")
                    audio_stream = next(stream for stream in streams if stream["codec_type"] == "audio")
                    self.assertEqual(video_stream["pix_fmt"], "yuv420p")
                    self.assertEqual(audio_stream["codec_name"], "aac")

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
            QTimer.singleShot(4000, app.quit)
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
