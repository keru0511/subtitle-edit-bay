from __future__ import annotations

import json
import os
import struct
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPoint, QPointF, QProcess, Qt, QUrl, qInstallMessageHandler
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy, QTest

from src.audio_preview_cache import (
    AudioPreviewCacheResult,
    audio_preview_cache_entries,
    cached_audio_preview_paths,
)
from src.gui import EditBayBackend, build_font_choices
from src.gui_state import SourceSelection
from src.runtime_dependencies import RuntimeDependencyStatus
from src.subtitle_project import (
    MIN_SEGMENT_DURATION_SECONDS,
    assign_project_layout_rows,
    create_project,
    load_project,
    save_project,
)


class GuiEditorRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._workspace = tempfile.TemporaryDirectory()
        cls._workspace_root = Path(cls._workspace.name)
        cls.app = EditBayBackend([], workspace_root=cls._workspace_root)
        cls._base_settings = deepcopy(cls.app.settings)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.autosave_timer.stop()
        cls.app.elapsed_timer.stop()
        cls.app._shutdown_executor()
        cls._workspace.cleanup()

    def setUp(self) -> None:
        self.root = self._workspace_root / self._testMethodName
        self.root.mkdir(parents=True)
        self._engines: list[QQmlApplicationEngine] = []

        app = self.app
        app.autosave_timer.stop()
        app.elapsed_timer.stop()
        app.workspace_root = self.root
        app.gui_config_path = self.root / ".gui" / "runtime_config.json"
        app.color_config_path = self.root / "assets" / "speaker_colors.json"
        app._project = None
        app._project_path = ""
        app._project_dirty = False
        app._undo_stack.clear()
        app._redo_stack.clear()
        app._selected_segment_index = -1
        app._active_job = ""
        app._ass_path = ""
        app._loading_project_sources = False
        app._relinking_project_sources = False
        app._source_selection = SourceSelection()
        app._speakers = []
        app._audio_tracks = app._default_audio_tracks()
        app._alignment_result = app._empty_alignment_result()
        app._alignment_busy = False
        app._dependencies = RuntimeDependencyStatus(ffmpeg=True, ffprobe=True, whisperx=True, cuda=True)
        app._settings = deepcopy(self._base_settings)
        app._running = False
        app._status = "保存済み"
        app._stage = "READY"
        app._progress = 0.0
        app._log = ""
        app._elapsed_seconds = 0
        app._cancel_requested = False
        app._audio_master_mixer.stop()
        app._audio_preview_gains.clear()
        app._audio_preview_levels.clear()
        app._audio_preview_pending_levels.clear()
        app._audio_preview_level_timer.stop()
        app._audio_preview_cache_request += 1
        app._audio_preview_cache_paths.clear()
        app._audio_preview_cache_future = None
        app._audio_preview_preparing = False
        app.audio_preview_cache_root = self.root / ".audio-preview-cache"
        self._media_probe_patch = patch.object(
            app,
            "_is_supported_media_file",
            side_effect=self._fake_media_file_has_required_streams,
        )
        self._media_probe_patch.start()

    def tearDown(self) -> None:
        for engine in self._engines:
            for window in engine.rootObjects():
                window.close()
                window.deleteLater()
            engine.clearComponentCache()
            engine.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.app.autosave_timer.stop()
        self.app.elapsed_timer.stop()
        self.app._project_dirty = False
        self.app._running = False
        self.app._active_job = ""
        self.app._cancel_requested = False

        self._media_probe_patch.stop()

    @staticmethod
    def _fake_media_file_has_required_streams(
        source: Path | str,
        required_streams: set[str],
        _label: str,
    ) -> bool:
        ext = Path(source).suffix.lower()
        video_exts = {".avi", ".m2ts", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv"}
        audio_exts = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}
        if ext in video_exts:
            return "video" in required_streams
        if ext in audio_exts:
            return "audio" in required_streams
        return False

    def _make_project(
        self,
        *,
        segments: list[dict[str, object]] | None = None,
        include_missing_audio: bool = False,
    ) -> tuple[Path, Path, Path]:
        video = self.root / "game.mkv"
        video.write_bytes(b"video")
        audio = self.root / "1-alice.flac"
        audio.write_bytes(b"audio")
        output = self.root / "export"
        output.mkdir(exist_ok=True)

        speakers = [
            {
                "name": "Alice",
                "style": "Speaker_Alice",
                "file_name": audio.name,
                "track_key": "craig:Alice",
                "color": "#7FD957",
                "path": str(audio),
            },
            {
                "name": "Bob",
                "style": "Speaker_Bob",
                "file_name": "2-bob.flac",
                "track_key": "craig:Bob",
                "color": "#FFD966",
                "path": str(self.root / "2-bob.flac"),
            },
        ]
        audio_sources = [{"path": str(audio)}]
        if include_missing_audio:
            audio_sources.append({"path": str(self.root / "missing.flac")})
        project = create_project(
            video_path=video,
            output_dir=output,
            audio_sources=audio_sources,
            speakers=speakers,
            segments=segments
            or [
                {
                    "id": "segment-a",
                    "start": 0,
                    "end": 4,
                    "text": "abcdefgh",
                    "speaker": "Speaker_Alice",
                    "words": [{"word": "abcdefgh", "start": 0, "end": 4}],
                }
            ],
            duration_seconds=30,
        )
        path = output / "game.subtitle-project.json"
        save_project(path, project)
        return path, video, audio

    def _load_project(self, **kwargs: object) -> Path:
        path, _, _ = self._make_project(**kwargs)
        self.assertTrue(self.app._load_project_path(path, update_sources=False))
        self._prime_audio_preview_cache()
        self.app.autosave_timer.stop()
        return path

    def _prime_audio_preview_cache(self) -> None:
        entries = audio_preview_cache_entries(
            self.app._project or {},
            self.app.audio_preview_cache_root,
        )
        for entry in entries:
            entry.output_path.parent.mkdir(parents=True, exist_ok=True)
            entry.output_path.write_bytes(b"cached-audio")
        self.app._audio_preview_cache_paths = cached_audio_preview_paths(entries)

    def _set_ready_sources(self) -> tuple[Path, Path, Path]:
        video = self.root / "game.mkv"
        video.write_bytes(b"video")
        audio = self.root / "1-alice.flac"
        audio.write_bytes(b"audio")
        output = self.root / "export"
        output.mkdir(exist_ok=True)
        with patch.object(self.app, "_probe_audio_tracks"):
            self.app.setVideoFile(str(video))
            self.app.setAudioFiles([str(audio)], False)
            self.app.setOutputDirectory(str(output))
        return video, audio, output

    def _load_qml(self) -> tuple[QQmlApplicationEngine, QObject]:
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("backend", self.app)
        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "Main.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path.resolve())))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        window.setWidth(1220)
        window.setHeight(760)
        self.app.processEvents()
        self._engines.append(engine)
        return engine, window

    def _quick_item(self, window: QObject, name: str) -> QQuickItem:
        item = window.findChild(QQuickItem, name)
        self.assertIsNotNone(item, name)
        return item

    def _quick_visual_item(self, root: QQuickItem, name: str) -> QQuickItem:
        if root.objectName() == name:
            return root
        for child in root.childItems():
            try:
                return self._quick_visual_item(child, name)
            except AssertionError:
                continue
        self.fail(name)

    def _click(self, window: QObject, item: QQuickItem) -> None:
        self.assertGreater(item.width(), 0, item.objectName())
        self.assertGreater(item.height(), 0, item.objectName())
        center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(
            window,
            Qt.MouseButton.LeftButton,
            pos=QPoint(round(center.x()), round(center.y())),
        )
        self.app.processEvents()

    def test_font_choices_are_sorted_deduplicated_and_include_default(self) -> None:
        choices = build_font_choices(["Yu Gothic", "@Yu Gothic", " arial ", "Arial", ""])

        self.assertEqual(choices[0]["family"], "")
        self.assertEqual([item["family"] for item in choices[1:]], ["arial", "Yu Gothic"])

    def test_project_load_restores_only_existing_sources(self) -> None:
        path, video, audio = self._make_project(include_missing_audio=True)

        with patch.object(self.app, "_probe_audio_tracks"):
            self.assertTrue(self.app._load_project_path(path, update_sources=True))

        self.assertEqual(self.app.sourceSelection["video"], str(video.resolve()))
        self.assertEqual(self.app.sourceSelection["output_dir"], str(path.parent.resolve()))
        self.assertEqual(self.app.sourceSelection["audio_files"], [str(audio.resolve())])
        self.assertEqual(self.app.selectedSegmentIndex, 0)
        self.assertEqual(self.app.stage, "EDIT")

    def test_project_load_clears_missing_sources(self) -> None:
        self._set_ready_sources()
        missing_video = self.root / "missing-video.mkv"
        missing_audio = self.root / "missing-audio.flac"
        missing_output = self.root / "missing-output"
        project = create_project(
            video_path=missing_video,
            output_dir=missing_output,
            audio_sources=[{"path": str(missing_audio)}],
            segments=[],
            duration_seconds=10,
            speakers=(),
        )
        missing_project = self.root / "missing.subtitle-project.json"
        save_project(missing_project, project)

        with patch.object(self.app, "_probe_audio_tracks"):
            self.assertTrue(self.app._load_project_path(missing_project, update_sources=True))

        self.assertEqual(self.app.sourceSelection["video"], "")
        self.assertEqual(self.app.sourceSelection["audio_files"], [])
        self.assertEqual(self.app.sourceSelection["output_dir"], "")

    def test_load_project_saves_pending_changes_before_switching(self) -> None:
        self._load_project()
        self.app._project_dirty = True
        target, _, _ = self._make_project()
        with patch.object(self.app, "saveProject", return_value=True) as save, patch.object(self.app, "_load_project_path") as loader:
            self.app.loadProject(str(target))
            save.assert_called_once()
            loader.assert_called_once_with(self.app._local_path(str(target)), update_sources=True)

    def test_load_project_refuses_switch_when_save_fails(self) -> None:
        self._load_project()
        self.app._project_dirty = True
        target, _, _ = self._make_project()
        with patch.object(self.app, "saveProject", return_value=False) as save, patch.object(self.app, "_load_project_path") as loader:
            self.app.loadProject(str(target))
            save.assert_called_once()
            loader.assert_not_called()

    def test_load_project_refuses_switch_when_project_has_no_path(self) -> None:
        self._load_project()
        self.app._project_dirty = True
        self.app._project_path = ""
        target, _, _ = self._make_project()
        with patch.object(self.app, "saveProject") as save, patch.object(self.app, "_load_project_path") as loader:
            self.app.loadProject(str(target))
            save.assert_not_called()
            loader.assert_not_called()

    def test_dropped_source_files_are_classified_as_video_and_audio(self) -> None:
        video = self.root / "capture.mkv"
        video.write_bytes(b"video")
        alice = self.root / "1-alice.flac"
        alice.write_bytes(b"audio")
        bob = self.root / "2-bob.wav"
        bob.write_bytes(b"audio")
        unsupported = self.root / "notes.txt"
        unsupported.write_text("ignore", encoding="utf-8")

        with patch.object(self.app, "_probe_audio_tracks"):
            self.app.importDroppedSourceFiles(
                [
                    QUrl.fromLocalFile(str(video.resolve())),
                    alice.resolve().as_uri(),
                    bob.resolve().as_uri(),
                    unsupported.resolve().as_uri(),
                ]
            )

        self.assertEqual(self.app.sourceSelection["video"], str(video.resolve()))
        self.assertEqual(
            self.app.sourceSelection["audio_files"],
            [str(alice.resolve()), str(bob.resolve())],
        )
        self.assertEqual([speaker["name"] for speaker in self.app.speakers], ["alice", "bob"])
        self.assertEqual(self.app.stage, "CHECK")
        self.assertIn("未対応", self.app.status)

    def test_drop_rejects_sources_while_processing(self) -> None:
        video = self.root / "capture.mkv"
        video.write_bytes(b"video")
        self.app._running = True

        self.app.importDroppedSourceFiles([video.resolve().as_uri()])

        self.assertEqual(self.app.sourceSelection["video"], "")
        self.assertEqual(self.app.stage, "BUSY")

    def test_video_file_dialog_allows_extended_video_extensions(self) -> None:
        video = self.root / "capture.avi"
        video.write_bytes(b"video")

        self.app.setVideoFile(str(video))

        self.assertEqual(self.app.sourceSelection["video"], str(video.resolve()))
        self.assertEqual(self.app.stage, "INPUT")

    def test_audio_file_dialog_allows_extended_audio_extensions(self) -> None:
        audio = self.root / "voice.opus"
        audio.write_bytes(b"audio")

        self.app.setAudioFiles([str(audio)], False)

        self.assertEqual(self.app.sourceSelection["audio_files"], [str(audio.resolve())])
        self.assertEqual(self.app.stage, "INPUT")

    def test_source_speaker_color_is_saved_and_reloaded(self) -> None:
        _, audio, _ = self._set_ready_sources()

        self.app.updateSpeakerColor(0, "#12abef")

        self.assertEqual(self.app.speakers[0]["color"], "#12ABEF")
        payload = json.loads(self.app.color_config_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["files"][audio.name]["color"], "#12ABEF")
        self.assertEqual(payload["speakers"]["alice"]["color"], "#12ABEF")

        self.app.setAudioFiles([str(audio)], False)
        self.assertEqual(self.app.speakers[0]["color"], "#12ABEF")

    def test_invalid_source_speaker_color_is_rejected(self) -> None:
        self._set_ready_sources()
        original = self.app.speakers[0]["color"]

        self.app.updateSpeakerColor(0, "not-a-color")

        self.assertEqual(self.app.speakers[0]["color"], original)
        self.assertEqual(self.app.stage, "ERROR")

    def test_project_speaker_color_updates_preview_waveform_and_project(self) -> None:
        self._load_project()
        self.app._project["waveforms"] = [
            {"style": "Speaker_Alice", "speaker": "Alice", "color": "#7FD957"}
        ]

        self.app.updateProjectSpeakerColor(0, "#445566")
        self.app.autosave_timer.stop()

        self.assertEqual(self.app.projectSpeakers[0]["color"], "#445566")
        self.assertEqual(self.app.subtitleWaveforms[0]["color"], "#445566")
        self.assertTrue(self.app.projectDirty)
        payload = json.loads(self.app.color_config_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["speakers"]["Alice"]["color"], "#445566")

    def test_audio_mixer_preview_applies_channel_state_gain_and_source_metadata(self) -> None:
        self._load_project()
        channels = self.app.audioMixerChannels
        self.assertEqual(channels[0]["preview_audio_track_index"], 0)
        self.assertTrue(channels[0]["preview_url"].startswith("file:"))
        self.assertEqual(channels[1]["preview_offset_seconds"], 0.0)

        preview = self.app.audioMixerPreviewChannels
        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0]["kind"], "video")
        self.assertEqual(preview[0]["preview_volume"], 1.0)
        structure_changes = QSignalSpy(self.app.audioMixerPreviewChannelsChanged)
        gain_changes = QSignalSpy(self.app.audioMixerPreviewGainsChanged)

        self.app.updateAudioMixChannel(0, {"volume_percent": 56})
        self.assertAlmostEqual(self.app.audioMixerPreviewGains[preview[0]["id"]], 0.56)
        self.assertEqual(structure_changes.count(), 0)
        self.assertEqual(gain_changes.count(), 1)

        self.app.updateAudioMixChannel(0, {"muted": True})
        muted_preview = self.app.audioMixerPreviewChannels
        self.assertEqual(len(muted_preview), 1)
        self.assertEqual(muted_preview[0]["preview_volume"], 0.0)
        self.assertEqual(structure_changes.count(), 0)
        self.assertEqual(gain_changes.count(), 2)

        self.app.updateAudioMixChannel(0, {"muted": False})
        self.app.updateAudioMixChannel(1, {"enabled": True, "solo": True})
        preview = self.app.audioMixerPreviewChannels
        self.assertEqual([channel["kind"] for channel in preview], ["video", "external"])
        self.assertEqual(preview[0]["preview_volume"], 0.0)
        self.assertEqual(preview[1]["preview_volume"], 1.0)
        self.assertEqual(structure_changes.count(), 1)
        self.assertEqual(gain_changes.count(), 4)

        sequence = self.app.audioMixerSequenceChannels
        self.assertEqual([channel["kind"] for channel in sequence], ["video", "external"])
        self.assertFalse(sequence[0]["audible"])
        self.assertTrue(sequence[1]["audible"])
        self.assertEqual(sequence[0]["duration_seconds"], 30.0)
        audio_format = QAudioFormat()
        audio_format.setSampleRate(48_000)
        audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.Int16)
        buffer = QAudioBuffer(
            struct.pack("<hhhh", 0, 16_384, -32_768, 8_192),
            audio_format,
        )
        channel_id = preview[1]["id"]
        self.assertEqual(self.app._audio_buffer_peak(buffer), 1.0)
        self.app._receive_audio_preview_buffer(channel_id, buffer)
        self.app._publish_audio_preview_levels()
        self.assertEqual(self.app.audioPreviewLevels[channel_id], 1.0)
        self.app._audio_preview_level_timer.stop()
        self.app.autosave_timer.stop()

    def test_audio_preview_gain_is_independent_of_active_channel_count(self) -> None:
        self._load_project()
        video_id = self.app.audioMixerChannels[0]["id"]
        external_id = self.app.audioMixerChannels[1]["id"]

        self.assertEqual(self.app.audioMixerPreviewGains[video_id], 1.0)

        self.app.updateAudioMixChannel(1, {"enabled": True})
        gains = self.app.audioMixerPreviewGains
        self.assertEqual(gains[video_id], 1.0)
        self.assertEqual(gains[external_id], 1.0)

        self.app.updateAudioMixChannel(0, {"volume_percent": 200})
        gains = self.app.audioMixerPreviewGains
        self.assertEqual(gains[video_id], 2.0)
        self.assertEqual(gains[external_id], 1.0)

        self.app.updateAudioMixChannel(1, {"enabled": False})
        self.assertEqual(self.app.audioMixerPreviewGains[video_id], 2.0)
        self.app.autosave_timer.stop()

    def test_audio_master_transport_and_metrics_are_exposed_to_qml(self) -> None:
        self._load_project()
        metrics_changed = QSignalSpy(self.app.audioMasterMetricsChanged)

        with (
            patch.object(self.app._audio_master_mixer, "play") as play,
            patch.object(self.app._audio_master_mixer, "seek") as seek,
            patch.object(self.app._audio_master_mixer, "stop") as stop,
        ):
            self.app.startAudioMixerPreview(1_250)
            self.app.seekAudioMixerPreview(2_500, True)
            self.app.pauseAudioMixerPreview()
            play.assert_called_once_with(1_250)
            seek.assert_called_once_with(2_500, True)
            stop.assert_called_once_with()

        self.app._update_audio_master_metrics(0.75, 3.25)
        self.assertEqual(self.app.audioMasterLevel, 0.75)
        self.assertEqual(self.app.audioLimiterReductionDb, 3.25)
        self.assertEqual(metrics_changed.count(), 1)

    def test_audio_mixer_preparation_publishes_cache_without_dirtying_project(self) -> None:
        self._load_project()
        entries = audio_preview_cache_entries(
            self.app._project or {},
            self.app.audio_preview_cache_root,
        )
        for entry in entries:
            entry.output_path.unlink()
        self.app._audio_preview_cache_paths.clear()

        def fake_prepare(_project, _cache_root, protected_paths=None):
            paths: dict[str, str] = {}
            for entry in entries:
                entry.output_path.write_bytes(b"prepared-audio")
                paths[entry.channel_id] = str(entry.output_path)
            return AudioPreviewCacheResult(paths)

        with patch("src.gui.prepare_audio_preview_cache", side_effect=fake_prepare):
            self.app.prepareAudioMixerPreview()
            self.assertTrue(self.app.audioPreviewPreparing)
            deadline = time.monotonic() + 2
            while self.app.audioPreviewPreparing and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)

        self.assertFalse(self.app.audioPreviewPreparing)
        self.assertTrue(self.app.audioPreviewClockUrl.endswith(".mka"))
        self.assertEqual(set(self.app._audio_preview_cache_paths), {entry.channel_id for entry in entries})
        self.assertTrue(all(
            channel["preview_url"].endswith(".mka")
            for channel in self.app.audioMixerChannels
        ))
        self.assertFalse(self.app.projectDirty)

    def test_audio_mixer_preparation_protects_cached_paths(self) -> None:
        self._load_project()
        entries = audio_preview_cache_entries(
            self.app._project or {},
            self.app.audio_preview_cache_root,
        )
        for entry in entries:
            entry.output_path.write_bytes(b"prepared-audio")
        self.app._audio_preview_cache_paths = cached_audio_preview_paths(entries)
        entries[0].output_path.unlink()

        def fake_prepare(_project, _cache_root, protected_paths):
            expected_paths = {Path(path) for path in self.app._audio_preview_cache_paths.values()}
            self.assertGreater(len(expected_paths), 0)
            self.assertTrue(expected_paths.issuperset({Path(path) for path in protected_paths}))
            return AudioPreviewCacheResult(self.app._audio_preview_cache_paths)

        with patch("src.gui.prepare_audio_preview_cache", side_effect=fake_prepare):
            self.app.prepareAudioMixerPreview()
            deadline = time.monotonic() + 2
            while self.app.audioPreviewPreparing and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
                if not self.app.audioPreviewPreparing:
                    break

        self.assertFalse(self.app.audioPreviewPreparing)

    def test_clear_audio_preview_cache_slot_invokes_cleanup_and_clears_paths(self) -> None:
        self._load_project()
        self._prime_audio_preview_cache()
        self.assertGreater(len(self.app._audio_preview_cache_paths), 0)

        with patch("src.gui.clear_audio_preview_cache") as clear_cache:
            clear_cache.return_value = (0, 0)
            self.app.clearAudioPreviewCache()

        clear_cache.assert_called_once_with(self.app.audio_preview_cache_root)
        self.assertFalse(self.app._audio_preview_cache_paths)
        self.assertFalse(self.app.audioPreviewPreparing)

    def test_audio_mixer_updates_individual_source_and_resets(self) -> None:
        path = self._load_project()
        self.assertEqual(len(self.app.audioMixerChannels), 2)

        self.app.updateAudioMixChannel(
            1,
            {"enabled": True, "volume_percent": 135, "muted": False, "solo": True},
        )
        self.app.autosave_timer.stop()

        channel = self.app.audioMixerChannels[1]
        self.assertTrue(channel["enabled"])
        self.assertEqual(channel["volume_percent"], 135)
        self.assertTrue(channel["solo"])
        self.assertTrue(self.app._project["audio_mix"]["customized"])
        self.assertTrue(self.app.saveProject())
        self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["audio_mix"]["customized"])

        self.app.resetAudioMixer()
        self.app.autosave_timer.stop()
        self.assertFalse(self.app._project["audio_mix"]["customized"])
        self.assertFalse(self.app.audioMixerChannels[1]["enabled"])

    def test_segment_field_edits_set_manual_metadata_and_clamp_values(self) -> None:
        self._load_project()

        self.app.updateSegment(
            0,
            {
                "text": " edited ",
                "start": -2,
                "end": -2,
                "speaker": "Speaker_Bob",
                "subtitle_font_scale": 9,
                "subtitle_font_family": "Yu Mincho",
            },
        )

        segment = self.app.subtitleSegments[0]
        self.assertEqual(segment["text"], "edited")
        self.assertEqual(segment["start"], 0)
        self.assertEqual(segment["end"], MIN_SEGMENT_DURATION_SECONDS)
        self.assertEqual(segment["speaker"], "Speaker_Bob")
        self.assertEqual(segment["source_speaker"], "Bob")
        self.assertEqual(segment["subtitle_font_scale"], 4.0)
        self.assertEqual(segment["subtitle_font_family"], "Yu Mincho")
        self.assertTrue(segment["manual_text"])
        self.assertTrue(segment["manual_timing"])
        self.assertTrue(segment["manual_speaker"])
        self.assertTrue(segment["manual_font_scale"])
        self.assertTrue(segment["manual_font_family"])
        self.assertNotIn("words", segment)

    def test_multiline_text_is_saved_and_preview_uses_formatted_newlines(self) -> None:
        self._load_project(
            segments=[
                {
                    "id": "multiline",
                    "start": 0,
                    "end": 3,
                    "text": "alpha beta gamma",
                    "speaker": "Speaker_Alice",
                    "max_width": 8,
                }
            ]
        )

        automatic = self.app.activeSubtitleSegments(1.0)[0]
        self.assertIn("\n", automatic["preview_text"])
        self.assertEqual(
            self.app.formatSubtitlePreview(0, "manual first\nmanual second"),
            "manual first\nmanual second",
        )

        self.app.updateSegment(0, {"text": "manual first\nmanual second"})
        saved = self.app.subtitleSegments[0]
        preview = self.app.activeSubtitleSegments(1.0)[0]
        self.assertEqual(saved["text"], "manual first\nmanual second")
        self.assertEqual(preview["preview_text"], "manual first\nmanual second")
        self.assertTrue(saved["manual_text"])

    def test_invalid_numeric_edits_preserve_segment_and_report_check(self) -> None:
        self._load_project()
        original = deepcopy(self.app.subtitleSegments[0])

        self.app.updateSegment(0, {"start": "not-a-number"})
        self.assertEqual(self.app.subtitleSegments[0], original)
        self.assertEqual(self.app.stage, "CHECK")

        self.app.updateSegment(0, {"subtitle_font_scale": float("nan")})
        self.assertEqual(self.app.subtitleSegments[0], original)
        self.assertEqual(self.app.stage, "CHECK")

    def test_add_delete_and_selection_round_trip(self) -> None:
        self._load_project()

        self.app.addSegment(5.0)
        self.assertEqual(len(self.app.subtitleSegments), 2)
        self.assertEqual(self.app.selectedSegmentIndex, 1)
        added = self.app.subtitleSegments[1]
        self.assertEqual(added["start"], 5.0)
        self.assertEqual(added["end"], 7.0)
        self.assertEqual(added["speaker"], "Speaker_Alice")

        self.app.deleteSelectedSegment()
        self.assertEqual(len(self.app.subtitleSegments), 1)
        self.assertEqual(self.app.selectedSegmentIndex, 0)

    def test_split_accepts_middle_and_rejects_segment_boundaries(self) -> None:
        self._load_project()

        self.app.splitSelectedSegment(2.0)
        segments = self.app.subtitleSegments
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["end"], 2.0)
        self.assertEqual(segments[1]["start"], 2.0)
        self.assertEqual(segments[0]["text"] + segments[1]["text"], "abcdefgh")
        self.assertEqual(self.app.selectedSegmentIndex, 1)

        self.app.selectSegment(0)
        self.app.splitSelectedSegment(0.01)
        self.assertEqual(len(self.app.subtitleSegments), 2)
        self.assertEqual(self.app.stage, "CHECK")

    def test_timeline_move_and_resize_snap_to_grid_and_neighbor_edges(self) -> None:
        self._load_project(
            segments=[
                {"id": "first", "start": 0, "end": 1, "text": "first", "speaker": "Speaker_Alice"},
                {"id": "second", "start": 2, "end": 3, "text": "second", "speaker": "Speaker_Bob"},
            ]
        )

        self.app.moveSegment(0, 1.96, 2.96, 0.1)
        self.assertEqual((self.app.subtitleSegments[0]["start"], self.app.subtitleSegments[0]["end"]), (2.0, 3.0))

        self.app.undoSubtitleEdit()
        self.app.resizeSegmentEnd(0, 1.96, 0.1)
        self.assertEqual(self.app.subtitleSegments[0]["end"], 2.0)

        self.app.undoSubtitleEdit()
        self.app.resizeSegmentStart(1, 1.04, 0.1)
        self.assertEqual(self.app.subtitleSegments[1]["start"], 1.0)

    def test_undo_redo_save_and_write_failures_are_guarded(self) -> None:
        path = self._load_project()

        self.app.openOutputFolder()
        self.assertEqual(self.app.stage, "CHECK")
        self.assertIn("出力先フォルダ", self.app.status)

        self.app.updateSegment(0, {"text": "after"})
        self.app.undoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments[0]["text"], "abcdefgh")
        self.app.redoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments[0]["text"], "after")
        self.assertTrue(self.app.saveProject())
        self.assertEqual(load_project(path)["segments"][0]["text"], "after")

        self.app.updateSegment(0, {"text": "unsaved"})
        with patch("src.gui.save_project", side_effect=OSError("disk full")):
            self.assertFalse(self.app.saveProject())
            self.assertEqual(self.app.stage, "ERROR")

        with (
            patch("src.gui.save_project", side_effect=OSError("disk full")),
            patch("src.gui.build_project_ass") as build_ass,
        ):
            self.app.buildSubtitlePreview(self.app.settings)
            build_ass.assert_not_called()

        with (
            patch.object(self.app, "saveSettings"),
            patch("src.gui.save_project", side_effect=OSError("disk full")),
            patch.object(self.app, "_start_command") as start,
        ):
            self.app.renderVideo(self.app.settings)
            start.assert_not_called()

    def test_history_stores_only_changed_segments_and_reuses_unchanged_entries(self) -> None:
        segments = [
            {
                "id": f"segment-{index}",
                "start": index * 1.5,
                "end": index * 1.5 + 1.0,
                "text": f"caption-{index}",
                "speaker": "Speaker_Alice",
            }
            for index in range(500)
        ]
        self._load_project(segments=segments)
        untouched = self.app._project["segments"][250]

        self.app.updateSegment(0, {"text": "changed"})

        self.assertEqual(len(self.app._undo_stack), 1)
        entry = self.app._undo_stack[0]
        self.assertEqual([item["id"] for item in entry["before"]], ["segment-0"])
        self.assertEqual([item["id"] for item in entry["after"]], ["segment-0"])
        current_untouched = next(item for item in self.app._project["segments"] if item["id"] == "segment-250")
        self.assertIs(current_untouched, untouched)

    def test_diff_history_restores_overlap_layout_rows(self) -> None:
        self._load_project(
            segments=[
                {"id": "first", "start": 0, "end": 4, "text": "first", "speaker": "Speaker_Alice"},
                {"id": "second", "start": 1, "end": 3, "text": "second", "speaker": "Speaker_Bob"},
                {"id": "third", "start": 3, "end": 5, "text": "third", "speaker": "Speaker_Alice"},
            ]
        )
        original = self.app.subtitleSegments

        self.app.updateSegment(0, {"end": 1})
        edited = self.app.subtitleSegments
        self.assertNotEqual(
            [item["layout_row"] for item in edited],
            [item["layout_row"] for item in original],
        )

        self.app.undoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments, original)
        self.app.redoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments, edited)


    def test_diff_history_round_trips_add_delete_and_split(self) -> None:
        self._load_project()
        original = self.app.subtitleSegments

        self.app.addSegment(5.0)
        added = self.app.subtitleSegments
        self.app.undoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments, original)
        self.app.redoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments, added)

        self.app.deleteSelectedSegment()
        deleted = self.app.subtitleSegments
        self.app.undoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments, added)
        self.app.redoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments, deleted)

        self.app.selectSegment(0)
        self.app.splitSelectedSegment((deleted[0]["start"] + deleted[0]["end"]) / 2)
        split = self.app.subtitleSegments
        self.app.undoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments, deleted)
        self.app.redoSubtitleEdit()
        self.assertEqual(self.app.subtitleSegments, split)

    def test_subtitle_model_and_range_queries_expose_only_ui_fields(self) -> None:
        self._load_project(
            segments=[
                {
                    "id": "first",
                    "start": 0,
                    "end": 2,
                    "text": "first",
                    "speaker": "Speaker_Alice",
                    "words": [{"word": "first", "start": 0, "end": 2}],
                },
                {
                    "id": "second",
                    "start": 4,
                    "end": 5,
                    "text": "second",
                    "speaker": "Speaker_Bob",
                },
            ]
        )

        model = self.app._subtitle_model
        self.assertEqual(model.rowCount(), 2)
        first_index = model.index(0, 0)
        self.assertEqual(model.data(first_index, model.TextRole), "first")
        self.assertEqual(model.data(first_index, model.EditorTextRole), "first")

        active = self.app.activeSubtitleSegments(1.0)
        visible = self.app.visibleSubtitleSegments(3.5, 5.5)
        self.assertEqual([item["id"] for item in active], ["first"])
        self.assertEqual([item["id"] for item in visible], ["second"])
        self.assertNotIn("words", active[0])
        self.assertEqual(visible[0]["sourceIndex"], 1)

        self.app.updateSegment(0, {"text": "updated", "subtitle_font_family": "Yu Gothic"})
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.data(model.index(0, 0), model.TextRole), "updated")
        self.assertEqual(model.data(model.index(0, 0), model.FontFamilyRole), "Yu Gothic")

    def test_playback_time_selects_latest_active_subtitle_and_keeps_last_in_gaps(self) -> None:
        self._load_project(
            segments=[
                {"id": "first", "start": 0, "end": 3, "text": "first", "speaker": "Speaker_Alice"},
                {"id": "overlap", "start": 1, "end": 2, "text": "overlap", "speaker": "Speaker_Bob"},
                {"id": "later", "start": 5, "end": 6, "text": "later", "speaker": "Speaker_Alice"},
            ]
        )

        self.assertEqual(self.app.segmentIndexAtTime(0.5), 0)
        self.app.selectSegmentAtTime(1.5)
        self.assertEqual(self.app.selectedSegmentIndex, 1)
        self.app.selectSegmentAtTime(4.0)
        self.assertEqual(self.app.selectedSegmentIndex, 1)
        self.app.selectSegmentAtTime(5.5)
        self.assertEqual(self.app.selectedSegmentIndex, 2)

    def test_subtitle_model_tracks_a_timing_reorder_without_stale_rows(self) -> None:
        self._load_project(
            segments=[
                {"id": "first", "start": 0, "end": 1, "text": "first", "speaker": "Speaker_Alice"},
                {"id": "second", "start": 2, "end": 3, "text": "second", "speaker": "Speaker_Alice"},
                {"id": "third", "start": 4, "end": 5, "text": "third", "speaker": "Speaker_Alice"},
            ]
        )

        self.app.updateSegment(0, {"start": 5, "end": 6})

        model = self.app._subtitle_model
        ids = [
            model.data(model.index(index, 0), model.SegmentIdRole)
            for index in range(model.rowCount())
        ]
        self.assertEqual(ids, ["second", "third", "first"])
        self.assertEqual(self.app.selectedSegmentIndex, 2)

    def test_speaker_and_font_edits_skip_timeline_row_reflow(self) -> None:
        self._load_project()
        with patch("src.gui.assign_project_layout_rows", wraps=assign_project_layout_rows) as reflow:
            self.app.updateSegment(
                0,
                {"speaker": "Speaker_Bob", "subtitle_font_scale": 1.5, "subtitle_font_family": "Yu Mincho"},
            )
            reflow.assert_not_called()

            self.app.updateSegment(0, {"text": "layout changed"})
            reflow.assert_called_once()

    def test_async_autosave_coalesces_edits_and_keeps_snapshot_stable(self) -> None:
        self._load_project()
        started = threading.Event()
        release = threading.Event()
        saved_texts: list[str] = []

        def fake_save(path, project, **_kwargs):
            if not started.is_set():
                started.set()
                release.wait(timeout=2)
            saved_texts.append(project["segments"][0]["text"])
            return Path(path)

        with patch("src.gui.save_project", side_effect=fake_save):
            self.app.updateSegment(0, {"text": "first edit"})
            self.app.autosave_timer.stop()
            self.app._autosave_project()
            self.assertTrue(started.wait(timeout=1))

            self.app.updateSegment(0, {"text": "second edit"})
            self.app.autosave_timer.stop()
            self.app._autosave_project()
            release.set()

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                self.app.processEvents()
                future = self.app._autosave_future
                if len(saved_texts) >= 2 and (future is None or future.done()):
                    self.app.processEvents()
                    break
                time.sleep(0.01)

        self.assertEqual(saved_texts, ["first edit", "second edit"])
        self.assertFalse(self.app.projectDirty)

    def test_loading_project_restores_subtitle_settings_in_gui(self) -> None:
        path, _, _ = self._make_project()
        project = load_project(path)
        project["subtitle_settings"] = {
            "font_size": 100,
            "outline_color": "#123456",
            "outline_thickness": 6,
            "volume_scale_percent": 35,
            "max_gap_seconds": 0.2,
            "end_padding_seconds": 0.04,
            "min_duration_seconds": 0.5,
        }
        save_project(path, project)
        settings_changes = QSignalSpy(self.app.settingsChanged)

        self.assertTrue(self.app._load_project_path(path, update_sources=False))

        self.assertEqual(settings_changes.count(), 1)
        self.assertEqual(self.app.settings["subtitle_font_size"], 100)
        self.assertEqual(self.app.settings["subtitle_outline_color"], "#123456")
        self.assertEqual(self.app.settings["subtitle_outline_thickness"], 6)
        self.assertEqual(self.app.settings["subtitle_volume_scale_percent"], 35)
        self.assertEqual(self.app.settings["subtitle_max_gap_seconds"], 0.2)
        self.assertEqual(self.app.settings["subtitle_end_padding_seconds"], 0.04)
        self.assertEqual(self.app.settings["subtitle_min_duration_seconds"], 0.5)
        self.assertFalse(self.app.projectDirty)

        _, window = self._load_qml()
        self.assertEqual(self._quick_item(window, "fontSizeSpin").property("value"), 200)
        self.assertEqual(self._quick_item(window, "outlineColorButton").property("colorValue"), "#123456")
        self.assertEqual(self._quick_item(window, "outlineThicknessSpin").property("value"), 6)
        self.assertEqual(self._quick_item(window, "volumeScaleSpin").property("value"), 35)
        self.assertEqual(window.property("selectedSubtitleFontSize"), 100)
        caption = self._quick_visual_item(
            window.contentItem(),
            "mainSubtitleOverlayCaption-0",
        )
        self.assertEqual(caption.property("font").pixelSize(), 44)

    def test_preview_updates_project_settings_and_ass_path(self) -> None:
        path = self._load_project()
        ass_path = self.root / "game.edited.ass"
        settings = {
            **self.app.settings,
            "subtitle_font_size": 72,
            "subtitle_outline_color": "#345678",
            "subtitle_outline_thickness": 8,
            "subtitle_volume_scale_percent": 35,
            "subtitle_max_gap_seconds": 0.2,
            "subtitle_end_padding_seconds": 0.04,
            "subtitle_min_duration_seconds": 0.5,
        }

        with patch("src.gui.build_project_ass", return_value=ass_path):
            self.app.buildSubtitlePreview(settings)

        subtitle = load_project(path)["subtitle_settings"]
        self.assertEqual(subtitle["font_size"], 72)
        self.assertEqual(subtitle["outline_color"], "#345678")
        self.assertEqual(subtitle["outline_thickness"], 8)
        self.assertEqual(subtitle["volume_scale_percent"], 35)
        self.assertEqual(subtitle["max_gap_seconds"], 0.2)
        self.assertEqual(self.app.assPath, str(ass_path.resolve()))
        self.assertEqual(self.app.stage, "ASS")

    def test_transcription_validates_dependencies_and_required_sources(self) -> None:
        with patch.object(self.app, "refreshDependencies"):
            self.app._dependencies = RuntimeDependencyStatus(False, True, True)
            self.app.startTranscription(self.app.settings)
            self.assertEqual(self.app.stage, "SETUP")

            self.app._dependencies = RuntimeDependencyStatus(True, True, True, cuda=False)
            self.app.startTranscription({**self.app.settings, "device": "cuda"})
            self.assertEqual(self.app.stage, "SETUP")
            self.assertIn("CUDA", self.app.status)

            self.app._dependencies = RuntimeDependencyStatus(True, True, True, cuda=True)
            self.app.startTranscription(self.app.settings)
            self.assertEqual(self.app.stage, "CHECK")
            self.assertIn("出力先", self.app.status)

    def test_transcription_starts_with_video_audio_only(self) -> None:
        video = self.root / "game.mkv"
        video.write_bytes(b"video")
        output = self.root / "export"
        output.mkdir(exist_ok=True)
        self.app.setVideoFile(str(video))
        self.app.setOutputDirectory(str(output))
        self.app._speakers = []
        self.app._audio_tracks = [{"selector": "0:a:0", "label": "0:a:0 (aac / 2ch)"}]

        with (
            patch.object(self.app, "saveSettings"),
            patch.object(self.app, "_start_command") as start,
        ):
            self.app.startTranscription(self.app.settings)

        transcribe_command, transcribe_job, _ = start.call_args.args
        self.assertEqual(transcribe_job, "transcribe")
        self.assertIn("transcribe", transcribe_command)
        self.assertNotIn("--audio-file", transcribe_command)

    def test_transcription_reports_missing_video_audio_track(self) -> None:
        video = self.root / "game.mkv"
        video.write_bytes(b"video")
        output = self.root / "export"
        output.mkdir(exist_ok=True)
        self.app.setVideoFile(str(video))
        self.app.setOutputDirectory(str(output))
        self.app._speakers = []
        self.app._audio_tracks = [{"selector": "", "label": "No tracks"}]

        with patch.object(self.app, "_start_command") as start:
            self.app.startTranscription(self.app.settings)

        self.assertEqual(start.call_count, 0)
        self.assertEqual(self.app.stage, "CHECK")

    def test_transcription_and_render_start_independent_phase_commands(self) -> None:
        _, audio, _ = self._set_ready_sources()
        settings = {
            **self.app.settings,
            "reference_audio": str(audio),
            "reference_track": "0:a:1",
            "alignment_offset_adjustment": 0.25,
        }

        with (
            patch.object(self.app, "refreshDependencies"),
            patch.object(self.app, "saveSettings"),
            patch.object(self.app, "_start_command") as start,
        ):
            self.app.startTranscription(settings)

        transcribe_command, transcribe_job, _ = start.call_args.args
        self.assertEqual(transcribe_job, "transcribe")
        self.assertIn("transcribe", transcribe_command)
        self.assertNotIn("render", transcribe_command)
        self.assertIn("0:a:1", transcribe_command)

        self._load_project()
        with (
            patch.object(self.app, "saveSettings"),
            patch.object(self.app, "saveProject", return_value=True),
            patch.object(self.app, "_start_command") as start,
        ):
            self.app.renderVideo(settings)

        render_command, render_job, _ = start.call_args.args
        self.assertEqual(render_job, "render")
        self.assertIn("render", render_command)
        self.assertNotIn("--audio-file", render_command)

    def test_render_automatically_selects_the_available_video_encoder(self) -> None:
        self._load_project()

        for nvenc_available, expected_codec, expected_mode in (
            (True, "h264_nvenc", "GPU"),
            (False, "libx264", "CPU"),
        ):
            with self.subTest(nvenc_available=nvenc_available):
                self.app._dependencies = RuntimeDependencyStatus(
                    ffmpeg=True,
                    ffprobe=True,
                    whisperx=True,
                    cuda=True,
                    nvenc=nvenc_available,
                )
                with (
                    patch.object(self.app, "refreshDependencies"),
                    patch.object(self.app, "saveSettings") as save_settings,
                    patch.object(self.app, "_update_project_settings"),
                    patch.object(self.app, "saveProject", return_value=True),
                    patch.object(self.app, "_start_command") as start,
                ):
                    self.app.renderVideo(self.app.settings)

                effective_settings = save_settings.call_args.args[0]
                self.assertEqual(effective_settings["video_codec"], expected_codec)
                _, render_job, status = start.call_args.args
                self.assertEqual(render_job, "render")
                self.assertIn(expected_mode, status)

    def test_process_finish_handles_transcribe_render_cancel_and_error(self) -> None:
        video, _, output = self._set_ready_sources()
        path, _, _ = self._make_project()
        expected_path = output / "game.subtitle-project.json"
        if path != expected_path:
            save_project(expected_path, load_project(path))

        self.app._active_job = "transcribe"
        self.app._running = True
        self.app._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertTrue(self.app.projectLoaded)
        self.assertEqual(self.app.stage, "EDIT")
        self.assertIn("焼き付け", self.app.status)
        self.assertEqual(self.app.progress, 1.0)
        self.assertEqual(self.app.activeJob, "")
        self.assertEqual(Path(self.app.projectPath), expected_path.resolve())
        self.assertEqual(self.app.sourceSelection["video"], str(video.resolve()))

        self.app._active_job = "render"
        self.app._running = True
        self.app._process_finished(0, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.app.stage, "COMPLETE")

        self.app._active_job = "render"
        self.app._running = True
        self.app._cancel_requested = True
        self.app._process_finished(1, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.app.stage, "CANCELLED")

        self.app._active_job = "render"
        self.app._running = True
        self.app._cancel_requested = False
        self.app._process_finished(7, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.app.stage, "ERROR")
        self.assertIn("7", self.app.status)

    def test_delayed_alignment_result_does_not_hide_process_error(self) -> None:
        self.app._status = "WhisperX failed"
        self.app._stage = "ERROR"
        self.app._apply_alignment_result({"track": "0:a:1", "offset": 0.5})

        self.assertEqual(self.app.stage, "ERROR")
        self.assertEqual(self.app.status, "WhisperX failed")
        self.assertEqual(self.app.alignmentResult["track"], "0:a:1")

    def test_running_source_changes_are_blocked_and_reset_clears_project(self) -> None:
        self._load_project()
        original = self.root / "original.mkv"
        original.write_bytes(b"video")
        second = self.root / "second.mkv"
        second.write_bytes(b"video")
        self.app._source_selection = SourceSelection(video=str(original.resolve()))

        self.app._running = True
        self.app.setVideoFile(str(second))
        self.assertEqual(self.app.sourceSelection["video"], str(original.resolve()))
        self.assertEqual(self.app.stage, "BUSY")

        self.app._running = False
        self.app.resetSources()
        self.assertFalse(self.app.projectLoaded)
        self.assertEqual(self.app.sourceSelection["video"], "")

    def test_relinking_source_selection_updates_existing_project(self) -> None:
        path, _, _ = self._make_project()
        with patch.object(self.app, "_probe_audio_tracks"):
            self.assertTrue(self.app._load_project_path(path, update_sources=False))

        relocated = self.root / "relinked"
        relocated.mkdir()
        original_project = load_project(path)
        old_video = Path(original_project["video"]["path"])
        old_audio = Path(original_project["audio_sources"][0]["path"])
        new_video = relocated / old_video.name
        new_audio = relocated / old_audio.name
        new_output = relocated / "output"
        new_output.mkdir()
        new_video.write_bytes(b"video")
        new_audio.write_bytes(b"audio")

        self.app.beginSourceRelink()
        with patch.object(self.app, "_probe_audio_tracks"):
            self.app.setVideoFile(str(new_video))
            self.app.setAudioFiles([str(new_audio)], False)
            self.app.setOutputDirectory(str(new_output))
        self.app.relinkProjectSources()

        self.assertTrue(self.app.projectLoaded)
        self.assertTrue(self.app.projectDirty)
        self.assertEqual(self.app._project["segments"], original_project["segments"])
        self.assertEqual(self.app.sourceSelection["video"], str(new_video.resolve()))
        self.assertEqual(self.app.sourceSelection["output_dir"], str(new_output.resolve()))
        self.assertEqual(self.app._project["video"]["path"], str(new_video.resolve()))
        self.assertEqual(self.app._project["output_dir"], str(new_output.resolve()))
        self.assertEqual(
            [item["path"] for item in self.app._project["audio_sources"]],
            [str(new_audio.resolve())],
        )
        self.assertEqual(self.app.projectSpeakers[0]["path"], str(new_audio.resolve()))
        self.assertEqual(self.app.projectSpeakers[0]["style"], original_project["speakers"][0]["style"])
        self.assertEqual(self.app.projectSpeakers[0]["color"], original_project["speakers"][0]["color"])
        self.assertEqual(self.app.projectSpeakers[0]["track_key"], original_project["speakers"][0]["track_key"])
        self.app.finishSourceRelink()

    def test_finish_source_relink_clears_relinking_state(self) -> None:
        path, _, _ = self._make_project()
        with patch.object(self.app, "_probe_audio_tracks"):
            self.assertTrue(self.app._load_project_path(path, update_sources=False))

        self.app.beginSourceRelink()
        self.assertTrue(self.app._relinking_project_sources)
        self.app.finishSourceRelink()
        self.assertFalse(self.app._relinking_project_sources)
        self.assertTrue(self.app.projectLoaded)

    def test_qml_workflow_state_matrix(self) -> None:
        _, window = self._load_qml()
        transcribe = self._quick_item(window, "transcribeButton")
        edit = self._quick_item(window, "editSubtitlesButton")
        render = self._quick_item(window, "renderVideoButton")
        reason = self._quick_item(window, "workflowBlockReason")
        output = self._quick_item(window, "outputFolderButton")

        self.assertTrue(transcribe.isVisible())
        self.assertFalse(transcribe.isEnabled())
        self.assertFalse(edit.isVisible())
        self.assertFalse(render.isVisible())
        self.assertTrue(reason.isVisible())
        self.assertIn("素材設定で", reason.property("text"))
        self.assertFalse(output.isEnabled())

        self._set_ready_sources()
        self.assertTrue(transcribe.isEnabled())
        self.assertFalse(reason.isVisible())
        self.assertTrue(output.isEnabled())

        self.app._source_selection = SourceSelection()
        self.app._speakers = []
        self.app.sourceSelectionChanged.emit()
        self.app.speakersChanged.emit()

        path, _, _ = self._make_project()
        self.assertTrue(self.app._load_project_path(path, update_sources=False))
        self.app.processEvents()
        self.assertFalse(transcribe.isVisible())
        self.assertTrue(edit.isVisible())
        self.assertTrue(render.isVisible())
        self.assertIn("焼き付け", render.property("text"))
        self.assertTrue(edit.isEnabled())
        self.assertTrue(render.isEnabled())

        self.app._active_job = "render"
        self.app._running = True
        self.app.activeJobChanged.emit()
        self.app.runningChanged.emit()
        self.app.processEvents()
        self.assertFalse(edit.isEnabled())
        self.assertFalse(render.isEnabled())

    def test_subtitle_preview_multiplies_base_and_per_caption_sizes(self) -> None:
        self._load_project(
            segments=[
                {
                    "id": "segment-a",
                    "start": 0,
                    "end": 4,
                    "text": "preview",
                    "speaker": "Speaker_Alice",
                    "subtitle_font_scale": 1.5,
                }
            ]
        )
        _, window = self._load_qml()

        main_caption = self._quick_visual_item(
            window.contentItem(),
            "mainSubtitleOverlayCaption-0",
        )
        self.assertEqual(main_caption.property("font").pixelSize(), 33)

        self._quick_item(window, "fontSizeSpin").setProperty("value", 200)
        self.app.processEvents()

        self.assertEqual(window.property("selectedSubtitleFontSize"), 100)
        self.assertEqual(main_caption.property("font").pixelSize(), 66)

        self._click(window, self._quick_item(window, "editSubtitlesButton"))
        editor_caption = self._quick_visual_item(
            window.contentItem(),
            "editorSubtitleOverlayCaption-0",
        )
        self.assertEqual(editor_caption.property("font").pixelSize(), 66)

    def test_qml_multiline_editor_live_previews_and_saves_manual_break(self) -> None:
        self._load_project(
            segments=[
                {
                    "id": "segment-a",
                    "start": 0,
                    "end": 4,
                    "text": "alpha beta gamma",
                    "speaker": "Speaker_Alice",
                    "max_width": 8,
                }
            ]
        )
        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "editSubtitlesButton"))

        caption = self._quick_visual_item(
            window.contentItem(),
            "editorSubtitleOverlayCaption-0",
        )
        self.assertIn("\n", caption.property("text"))

        text_area = self._quick_visual_item(window.contentItem(), "captionTextArea")
        self.assertIn("\n", text_area.property("text"))
        text_area.forceActiveFocus()
        text_area.setProperty("text", "manual first\nmanual second")
        self.app.processEvents()
        self.assertEqual(caption.property("text"), "manual first\nmanual second")

        self._click(window, self._quick_item(window, "saveProjectButton"))
        self.assertEqual(self.app.subtitleSegments[0]["text"], "manual first\nmanual second")

    def test_qml_settings_round_trip_and_expanded_layout_fit(self) -> None:
        _, window = self._load_qml()
        self._load_project()
        panel = self._quick_item(window, "advancedSettingsPanel")
        toggle = self._quick_item(window, "settingsToggleButton")
        self.assertFalse(panel.isVisible())

        self._click(window, toggle)
        self.assertTrue(panel.isVisible())
        actions = self._quick_item(window, "workflowActions")
        self.assertLessEqual(panel.y() + panel.height(), actions.y() + 1)
        self.assertLessEqual(actions.y() + actions.height(), actions.parentItem().height() + 1)

        font_size = self._quick_item(window, "fontSizeSpin")
        self.assertEqual(font_size.property("to"), 900)
        font_size.setProperty("value", 900)
        self._quick_item(window, "outlineColorButton").setProperty("colorValue", "#456789")
        self._quick_item(window, "outlineThicknessSpin").setProperty("value", 9)
        self._quick_item(window, "volumeScaleSpin").setProperty("value", 30)
        self._click(window, self._quick_item(window, "saveSettingsButton"))
        self.assertEqual(self.app.settings["subtitle_font_size"], 450)
        self.assertEqual(self.app.settings["subtitle_outline_color"], "#456789")
        self.assertEqual(self.app.settings["subtitle_outline_thickness"], 9)
        self.assertEqual(self.app.settings["subtitle_volume_scale_percent"], 30)

        self._click(window, toggle)
        self.assertFalse(panel.isVisible())

    def test_qml_editor_content_is_loaded_only_when_opened(self) -> None:
        self._load_project()
        _, window = self._load_qml()
        self.assertIsNone(window.findChild(QQuickItem, "editorTimeline"))

        self._click(window, self._quick_item(window, "editSubtitlesButton"))

        self.assertIsNotNone(window.findChild(QQuickItem, "editorTimeline"))
        self.assertIsNotNone(window.findChild(QQuickItem, "projectSpeakerColorList"))

    def test_qml_timeline_instantiates_only_visible_captions(self) -> None:
        segments = [
            {
                "id": f"caption-{index}",
                "start": index * 1.5,
                "end": index * 1.5 + 1.0,
                "text": f"caption-{index}",
                "speaker": "Speaker_Alice",
            }
            for index in range(500)
        ]
        self._load_project(segments=segments)
        _, window = self._load_qml()

        self._click(window, self._quick_item(window, "editSubtitlesButton"))
        timeline = self._quick_item(window, "editorTimeline")
        visible = timeline.property("visibleSegments")
        if hasattr(visible, "toVariant"):
            visible = visible.toVariant()

        self.assertGreater(len(visible), 0)
        self.assertLess(len(visible), len(segments))
        self.assertEqual(visible[0]["sourceIndex"], 0)

    def test_qml_timeline_refresh_does_not_access_destroyed_segment_data(self) -> None:
        self._load_project()
        messages: list[str] = []
        previous_handler = qInstallMessageHandler(
            lambda _message_type, _context, message: messages.append(message)
        )
        try:
            _, window = self._load_qml()
            self._click(window, self._quick_item(window, "editSubtitlesButton"))
            timeline = self._quick_item(window, "editorTimeline")
            timeline.setProperty(
                "visibleSegments",
                [
                    {
                        "sourceIndex": 0,
                        "segment": {
                            "start": 0.0,
                            "end": 1.0,
                            "text": "caption",
                            "speaker": "Speaker_Alice",
                            "subtitle_font_family": "",
                        },
                    }
                ],
            )
            self.app.processEvents()
            timeline.setProperty("visibleSegments", [])
            self.app.processEvents()
        finally:
            qInstallMessageHandler(previous_handler)

        type_errors = [message for message in messages if "TypeError" in message]
        self.assertEqual(type_errors, [], "\n".join(type_errors))

    def test_qml_mixer_close_does_not_run_callbacks_in_destroyed_context(self) -> None:
        self._load_project()
        messages: list[str] = []
        previous_handler = qInstallMessageHandler(
            lambda _message_type, _context, message: messages.append(message)
        )
        try:
            _, window = self._load_qml()
            for _ in range(3):
                self._click(window, self._quick_item(window, "audioMixerOpenButton"))
                self.assertIsNotNone(window.findChild(QObject, "mixerPreviewPlayers"))
                self.app.updateAudioMixChannel(0, {"muted": True})
                self.app.updateAudioMixChannel(0, {"muted": False})
                self.app.processEvents()
                self._click(window, self._quick_item(window, "mixerBackButton"))
                QTest.qWait(80)
                self.app.processEvents()
        finally:
            qInstallMessageHandler(previous_handler)

        invalid_context_errors = [
            message
            for message in messages
            if "invalid context" in message or "syncPreviewPlayer" in message
        ]
        self.assertEqual(invalid_context_errors, [], chr(10).join(invalid_context_errors))

    def test_mixer_volume_change_preserves_horizontal_scroll(self) -> None:
        self.app._audio_tracks = [
            {"selector": f"0:a:{index}", "label": f"Track {index + 1}"}
            for index in range(8)
        ]
        self._load_project()
        for index in range(len(self.app.audioMixerChannels)):
            self.app.updateAudioMixChannel(index, {"enabled": True})
        self.app.autosave_timer.stop()
        volumes_before = [channel["volume_percent"] for channel in self.app.audioMixerChannels]

        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "audioMixerOpenButton"))
        channel_list = self._quick_item(window, "mixerChannelList")
        self.assertGreater(channel_list.property("contentWidth"), channel_list.width())
        self.assertEqual(
            self._quick_visual_item(channel_list, "mixerChannelStrip-0").width(),
            170,
        )

        sequence = self._quick_item(window, "mixerSequence")
        sequence.setProperty("viewportY", 60.0)
        self.app.processEvents()
        self.assertGreater(sequence.property("viewportY"), 0)
        lane_body = self._quick_visual_item(sequence, "timelineLaneBody-0")
        lane_label = self._quick_visual_item(sequence, "timelineLaneLabel-0")
        self.assertAlmostEqual(
            lane_body.mapToScene(QPointF(0, 0)).y(),
            lane_label.mapToScene(QPointF(0, 0)).y(),
            delta=0.5,
        )

        channel_list.setProperty("contentX", 420.0)
        self.app.processEvents()
        original_x = float(channel_list.property("contentX"))
        self.assertGreater(original_x, 0)

        visible_fader = None
        list_left = channel_list.mapToScene(QPointF(0, 0)).x()
        list_right = list_left + channel_list.width()
        stack = list(channel_list.childItems())
        while stack:
            item = stack.pop()
            stack.extend(item.childItems())
            if item.objectName() != "mixerChannelFader" or not item.isVisible():
                continue
            center_x = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).x()
            if list_left <= center_x <= list_right:
                visible_fader = item
                break
        self.assertIsNotNone(visible_fader)

        self._click(window, visible_fader)
        QTest.qWait(20)
        self.app.processEvents()
        self.assertAlmostEqual(float(channel_list.property("contentX")), original_x, delta=1.0)
        self.assertNotEqual(
            [channel["volume_percent"] for channel in self.app.audioMixerChannels],
            volumes_before,
        )
    def test_editor_render_action_returns_to_main_and_starts_render(self) -> None:
        self._load_project()
        _, window = self._load_qml()
        main = self._quick_item(window, "mainWorkspace")
        editor = self._quick_item(window, "editorPage")

        self._click(window, self._quick_item(window, "editSubtitlesButton"))
        render = self._quick_item(window, "editorRenderButton")
        self.assertIn("焼き付け", render.property("text"))

        with (
            patch.object(self.app, "saveSettings"),
            patch.object(self.app, "saveProject", return_value=True),
            patch.object(self.app, "_start_command") as start,
        ):
            self._click(window, render)

        self.assertTrue(main.isVisible())
        self.assertFalse(editor.isVisible())
        render_command, render_job, _ = start.call_args.args
        self.assertEqual(render_job, "render")
        self.assertIn("render", render_command)

    def test_qml_source_popup_and_editor_toolbar_are_clickable_at_minimum_size(self) -> None:
        self._load_project()
        _, window = self._load_qml()

        self._click(window, self._quick_item(window, "sourceSetupButton"))
        popup = window.findChild(QObject, "sourcePopup")
        self.assertIsNotNone(popup)
        self.assertTrue(popup.property("opened"))
        self._click(window, self._quick_item(window, "sourceDoneButton"))
        self.assertFalse(popup.property("opened"))

        main = self._quick_item(window, "mainWorkspace")
        editor = self._quick_item(window, "editorPage")
        mixer = self._quick_item(window, "mixerPage")

        self._click(window, self._quick_item(window, "audioMixerOpenButton"))
        self.assertFalse(main.isVisible())
        self.assertFalse(editor.isVisible())
        self.assertTrue(mixer.isVisible())
        channel_list = self._quick_item(window, "mixerChannelList")
        self.assertEqual(channel_list.property("count"), 2)
        preview_players = window.findChild(QObject, "mixerPreviewPlayers")
        self.assertIsNotNone(preview_players)
        self.assertEqual(preview_players.property("count"), 1)
        preview_player = window.findChild(QObject, "mixerPreviewPlayer-video:0:a:0")
        self.assertIsNotNone(preview_player)
        video_channel_id = self.app.audioMixerChannels[0]["id"]
        video_channel_strip = self._quick_visual_item(channel_list, "mixerChannelStrip-0")
        video_mute_button = self._quick_visual_item(video_channel_strip, "mixerMuteButton")

        self.assertTrue(QMetaObject.invokeMethod(video_mute_button, "clicked"))
        self.app.processEvents()
        self.assertEqual(preview_players.property("count"), 1)
        self.assertIs(
            window.findChild(QObject, "mixerPreviewPlayer-video:0:a:0"),
            preview_player,
        )
        self.assertEqual(self.app.audioMixerPreviewGains[video_channel_id], 0.0)
        video_channel_strip = self._quick_visual_item(channel_list, "mixerChannelStrip-0")
        video_mute_button = self._quick_visual_item(video_channel_strip, "mixerMuteButton")

        self.assertTrue(QMetaObject.invokeMethod(video_mute_button, "clicked"))
        self.app.processEvents()
        self.assertEqual(preview_players.property("count"), 1)
        self.assertIs(
            window.findChild(QObject, "mixerPreviewPlayer-video:0:a:0"),
            preview_player,
        )
        self.assertEqual(self.app.audioMixerPreviewGains[video_channel_id], 1.0)
        cache_summary = self._quick_item(window, "mixerAudioPreviewCacheSummary")
        cache_clear = self._quick_item(window, "mixerClearAudioPreviewCacheButton")
        self.assertIn("/", cache_summary.property("text"))
        self.assertGreater(cache_clear.width(), 0)

        mixer_items = [
            channel_list,
            self._quick_item(window, "mixerPlayButton"),
            self._quick_item(window, "mixerRewindButton"),
            self._quick_item(window, "mixerSeek"),
            self._quick_item(window, "mixerForwardButton"),
            self._quick_item(window, "mixerTimeText"),
            cache_clear,
            self._quick_item(window, "mixerSequence"),
            self._quick_visual_item(self._quick_item(window, "mixerSequence"), "mixerSequenceVolumeBar"),
            self._quick_visual_item(channel_list, "mixerChannelFader"),
            self._quick_visual_item(channel_list, "mixerMuteButton"),
            self._quick_visual_item(channel_list, "mixerSoloButton"),
            self._quick_visual_item(channel_list, "mixerChannelEnabledCheck"),
            self._quick_item(window, "mixerResetButton"),
            self._quick_item(window, "mixerSaveButton"),
            self._quick_item(window, "mixerToEditorButton"),
            self._quick_item(window, "mixerRenderButton"),
            self._quick_item(window, "mixerBackButton"),
        ]
        for item in mixer_items:
            name = item.objectName()
            top_left = item.mapToScene(QPointF(0, 0))
            self.assertGreater(item.width(), 0, name)
            self.assertGreater(item.height(), 0, name)
            self.assertGreaterEqual(top_left.x(), 0, name)
            self.assertGreaterEqual(top_left.y(), 0, name)
            self.assertLessEqual(top_left.x() + item.width(), window.width() + 1, name)
            self.assertLessEqual(top_left.y() + item.height(), window.height() + 1, name)

        self._click(window, self._quick_item(window, "mixerBackButton"))
        self.assertTrue(main.isVisible())
        self.assertFalse(mixer.isVisible())

        self._click(window, self._quick_item(window, "editSubtitlesButton"))
        self.assertFalse(main.isVisible())
        self.assertTrue(editor.isVisible())
        self.assertFalse(mixer.isVisible())

        toolbar_names = [
            "undoCaptionButton",
            "redoCaptionButton",
            "addCaptionButton",
            "splitCaptionButton",
            "deleteCaptionButton",
            "saveProjectButton",
            "buildAssButton",
            "editorRenderButton",
            "editorBackButton",
        ]
        for name in toolbar_names:
            item = self._quick_item(window, name)
            top_left = item.mapToScene(QPointF(0, 0))
            self.assertGreater(item.width(), 0, name)
            self.assertGreaterEqual(top_left.x(), 0, name)
            self.assertGreaterEqual(top_left.y(), 0, name)
            self.assertLessEqual(top_left.x() + item.width(), window.width() + 1, name)
            self.assertLessEqual(top_left.y() + item.height(), window.height() + 1, name)

        self._click(window, self._quick_item(window, "editorBackButton"))
        self.assertTrue(main.isVisible())
        self.assertFalse(editor.isVisible())

    def test_qml_transcription_dictionary_uses_dedicated_screen_and_saves(self) -> None:
        _, window = self._load_qml()
        main = self._quick_item(window, "mainWorkspace")
        page = self._quick_item(window, "transcriptionDictionaryPage")

        self.assertTrue(main.isVisible())
        self.assertFalse(page.isVisible())
        self._click(window, self._quick_item(window, "transcriptionDictionaryOpenButton"))
        self.assertFalse(main.isVisible())
        self.assertTrue(page.isVisible())

        title_field = self._quick_visual_item(window.contentItem(), "transcriptionGameTitleField")
        title_field.forceActiveFocus()
        title_field.setProperty("text", "Test Game")
        self.app.processEvents()

        self._click(window, self._quick_item(window, "transcriptionDictionaryBackButton"))
        self.assertTrue(main.isVisible())
        self.assertFalse(page.isVisible())
        self.assertEqual(self.app.transcriptionContext["game_title"], "Test Game")
        self.assertTrue(self.app.gui_config_path.is_file())


if __name__ == "__main__":
    unittest.main()
