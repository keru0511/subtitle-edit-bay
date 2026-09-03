from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QMetaObject, QObject, QPointF, QProcess, Qt, QUrl
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy, QTest

from src.audio_preview_cache import (
    AudioPreviewCacheResult,
    audio_preview_cache_entries,
    cached_audio_preview_paths,
)
from src import updater
from src.codex_runtime import CodexRuntimeInfo
from src.gui import build_font_choices
from src.gui_codex_chat_state import CodexChatSnapshot
from src.gui_codex_state import CodexSessionSnapshot
from src.gui_state import SourceSelection
from src.runtime_dependencies import RuntimeDependencyStatus
from src.subtitle_project import (
    MIN_SEGMENT_DURATION_SECONDS,
    assign_project_layout_rows,
    create_project,
    load_project,
    save_project,
)
from tests.edit_bay_gui_test_session import EditBayGuiTestSession
from tests.gui_test_harness import GuiTestHarness


class GuiEditorRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._session = EditBayGuiTestSession()
        cls.app = cls._session.backend
        cls._codex_chat_connect_calls = cls._session.codex_chat_connect_calls
        cls._startup_log_text = cls._session.startup_log_text

    @classmethod
    def tearDownClass(cls) -> None:
        cls._session.cleanup()

    def setUp(self) -> None:
        self.addCleanup(self._session.finish_test)
        self.root = self._session.prepare_test(self._testMethodName)
        app = self.app
        self._media_probe_patch = patch.object(
            app,
            "_is_supported_media_file",
            side_effect=self._fake_media_file_has_required_streams,
        )
        self._media_probe_patch.start()
        self.addCleanup(self._media_probe_patch.stop)
        qml_root = Path(__file__).resolve().parents[1] / "src" / "ui"
        self.gui = GuiTestHarness(self.app, backend=self.app, qml_roots=(qml_root,))
        self.addCleanup(self.gui.cleanup)

    @staticmethod
    def _fake_media_file_has_required_streams(
        source: Path | str,
        required_streams: set[str],
        _label: str,
    ) -> tuple[bool, str]:
        ext = Path(source).suffix.lower()
        video_exts = {".avi", ".m2ts", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv"}
        audio_exts = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}
        if ext in video_exts:
            return ("video" in required_streams), ""
        if ext in audio_exts:
            return ("audio" in required_streams), ""
        return False, ""

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
        if not any(str(item.get("selector", "")).strip() for item in self.app._audio_tracks):
            self.app._audio_tracks = [{"selector": "0:a:0", "label": "0:a:0  game / 2ch"}]
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
        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "Main.qml"
        return self.gui.load_qml(qml_path)

    def _quick_item(self, window: QObject, name: str) -> QQuickItem:
        return self.gui.find_item(window, name)

    def _quick_visual_item(self, root: QQuickItem, name: str) -> QQuickItem:
        return self.gui.find_visual_item(root, name)

    def _click(self, window: QObject, item: QQuickItem) -> None:
        self.gui.click(window, item)

    def _assert_quick_item_within(self, container: QQuickItem, item: QQuickItem) -> None:
        self.gui.assert_item_within(container, item)

    def _assert_button_content_fits(self, button: QQuickItem) -> None:
        content = button.property("contentItem")
        self.assertIsNotNone(content, button.objectName())
        self.assertLessEqual(content.property("implicitWidth"), button.width() + 1, button.objectName())

    def test_shared_backend_session_resets_state_before_each_test(self) -> None:
        first_root = self.root
        base_config = deepcopy(self.app._config)
        base_transcription_context = deepcopy(self.app.transcriptionContext)
        base_codex_session_snapshot = self.app._codex_session.snapshot
        self.app._project = {
            "segments": [
                {
                    "id": "leaked-segment",
                    "start": 0.0,
                    "end": 1.0,
                    "text": "leaked",
                }
            ]
        }
        self.app._subtitle_model.set_segments(self.app._project["segments"])
        self.app._source_selection = SourceSelection(video="leaked-video.mkv")
        self.app._active_job = "leaked-job"
        self.app._highlight_candidates = [{"id": "leaked-highlight"}]
        self.app._update_busy = True
        self.app._config = {"leaked": True}
        self.app._transcription_context = {
            **base_transcription_context,
            "game_title": "leaked-game",
        }
        codex_stop_event = threading.Event()
        leaked_codex_thread = threading.Thread(
            target=codex_stop_event.wait,
            name="leaked-codex-edit-session",
            daemon=True,
        )
        self.app._codex_session._stop_event = codex_stop_event
        self.app._codex_session._snapshot = CodexSessionSnapshot(state="running")
        self.app._codex_session._thread = leaked_codex_thread
        leaked_codex_thread.start()
        self.app._codex_chat._snapshot = CodexChatSnapshot(
            connection_state="ready",
            auth_state="authenticated",
            messages=({"role": "user", "text": "leaked"},),
        )
        self.app.autosave_timer.start(60_000)

        self._session.finish_test()
        self.root = self._session.prepare_test(f"{self._testMethodName}-repeat")

        self.assertNotEqual(self.root, first_root)
        self.assertIsNone(self.app._project)
        self.assertEqual(self.app._subtitle_model.rowCount(), 0)
        self.assertEqual(self.app.sourceSelection["video"], "")
        self.assertEqual(self.app._active_job, "")
        self.assertEqual(self.app._highlight_candidates, [])
        self.assertFalse(self.app._update_busy)
        self.assertEqual(self.app._config, base_config)
        self.assertEqual(self.app.transcriptionContext, base_transcription_context)
        self.assertFalse(leaked_codex_thread.is_alive())
        self.assertEqual(self.app._codex_session.snapshot, base_codex_session_snapshot)
        self.assertEqual(self.app._codex_chat.snapshot.auth_state, "unknown")
        self.assertEqual(self.app._codex_chat.snapshot.messages, ())
        self.assertFalse(self.app.autosave_timer.isActive())

    def test_empty_project_can_be_manually_edited_without_transcription_dependencies(self) -> None:
        video, _audio, output = self._set_ready_sources()
        with patch("src.gui.probe_media_duration", return_value=30.0):
            self.assertTrue(self.app.createEmptyProject())

        self.assertEqual(self.app.subtitleSegments, [])
        self.assertEqual(self.app.sourceSelection["video"], str(video.resolve()))
        self.app._dependencies = RuntimeDependencyStatus(
            ffmpeg=True,
            ffprobe=True,
            whisperx=False,
            cuda=False,
            nvenc=False,
        )
        self.app.dependenciesChanged.emit()

        _, window = self._load_qml()
        transcribe = self._quick_item(window, "transcribeButton")
        edit = self._quick_item(window, "editSubtitlesButton")
        render = self._quick_item(window, "renderVideoButton")
        self.assertFalse(transcribe.isEnabled())
        self.assertTrue(edit.isEnabled())
        self.assertTrue(render.isEnabled())

        self._click(window, edit)
        self.assertTrue(self._quick_item(window, "editorEmptyState").isVisible())
        self._click(window, self._quick_item(window, "addCaptionButton"))
        self.assertEqual(self.app.segmentCount, 1)
        self._click(window, self._quick_item(window, "saveProjectButton"))
        self.assertEqual(len(load_project(output / "game.subtitle-project.json")["segments"]), 1)

    def test_empty_project_creation_never_overwrites_existing_project(self) -> None:
        _video, _audio, output = self._set_ready_sources()
        project_path = output / "game.subtitle-project.json"
        sentinel = create_project(
            video_path=self.app.sourceSelection["video"],
            output_dir=output,
            segments=[{"start": 0, "end": 1, "text": "keep", "speaker": "Oz"}],
        )
        save_project(project_path, sentinel)

        with patch("src.gui.probe_media_duration", return_value=30.0):
            self.assertFalse(self.app.createEmptyProject())
        self.assertEqual(load_project(project_path)["segments"][0]["text"], "keep")

    def test_video_only_empty_project_disables_mixer_before_normal_render(self) -> None:
        video = self.root / "video-only.mkv"
        video.write_bytes(b"video")
        output = self.root / "export"
        output.mkdir()
        with patch.object(self.app, "_probe_audio_tracks"):
            self.app.setVideoFile(str(video))
            self.app.setOutputDirectory(str(output))
        self.app._audio_tracks = [{"selector": "", "label": "音声トラックなし"}]
        self.app._speakers = []

        with patch("src.gui.probe_media_duration", return_value=1.0):
            self.assertTrue(self.app.createEmptyProject())

        self.assertEqual(self.app.audioMixerChannels, [])
        self.assertFalse(self.app.audioMixerAvailable)
        _, window = self._load_qml()
        mixer_button = self._quick_item(window, "audioMixerOpenButton")
        self.assertFalse(mixer_button.property("enabled"))
        self.assertEqual(mixer_button.property("text"), "音声トラックなし")

        self.app.updateAudioMixChannel(0, {"volume_percent": 150})
        self.assertFalse(self.app._project["audio_mix"]["customized"])

        with (
            patch.object(self.app, "refreshDependencies"),
            patch.object(self.app, "saveProject", return_value=True),
            patch.object(self.app, "_start_command") as start_command,
        ):
            self.app.renderVideo(self.app.settings)

        self.assertEqual(start_command.call_args.args[1], "render")

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

    def test_project_save_restart_reload_e2e_preserves_edits_and_unsaved_switch(self) -> None:
        project_path = self._load_project()
        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "editSubtitlesButton"))
        QTest.qWait(100)

        caption = self._quick_visual_item(
            self._quick_item(window, "captionTable"),
            "captionTextArea",
        )
        caption.forceActiveFocus()
        caption.setProperty("text", "saved across restart")
        self.app.processEvents()
        self._click(window, self._quick_item(window, "saveProjectButton"))

        self.app.updateSegment(
            0,
            {
                "start": 1.25,
                "end": 3.5,
                "speaker": "Speaker_Bob",
                "subtitle_font_scale": 1.45,
                "subtitle_font_family": "Yu Mincho",
            },
        )
        self._click(window, self._quick_item(window, "saveProjectButton"))

        saved_segment = load_project(project_path)["segments"][0]
        self.assertEqual(saved_segment["text"], "saved across restart")
        self.assertEqual(saved_segment["start"], 1.25)
        self.assertEqual(saved_segment["end"], 3.5)
        self.assertEqual(saved_segment["speaker"], "Speaker_Bob")
        self.assertEqual(saved_segment["subtitle_font_scale"], 1.45)
        self.assertEqual(saved_segment["subtitle_font_family"], "Yu Mincho")

        result_path = self.root / "reloaded-project.json"
        process_python = shutil.which("python.exe" if os.name == "nt" else "python3") or sys.executable
        probe = subprocess.run(
            [
                process_python,
                "-u",
                "-m",
                "tests.project_reload_probe",
                "--project",
                str(project_path),
                "--result",
                str(result_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONUTF8": "1"},
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        reloaded = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertTrue(reloaded["project_loaded"])
        self.assertTrue(reloaded["qml_loaded"])
        self.assertTrue(reloaded["edit_button_enabled"])
        self.assertFalse(reloaded["project_dirty"])
        self.assertEqual(Path(reloaded["project_path"]).resolve(), project_path.resolve())
        self.assertEqual(reloaded["segments"][0]["text"], "saved across restart")
        self.assertEqual(reloaded["segments"][0]["start"], 1.25)
        self.assertEqual(reloaded["segments"][0]["end"], 3.5)
        self.assertEqual(reloaded["segments"][0]["speaker"], "Speaker_Bob")
        self.assertEqual(reloaded["segments"][0]["subtitle_font_scale"], 1.45)
        self.assertEqual(reloaded["segments"][0]["subtitle_font_family"], "Yu Mincho")

        other_project = deepcopy(load_project(project_path))
        other_project["segments"][0] = {
            **other_project["segments"][0],
            "id": "other-project-segment",
            "text": "other project",
        }
        other_path = self.root / "other.subtitle-project.json"
        save_project(other_path, other_project)

        self.app.updateSegment(0, {"text": "saved before project switch"})
        self.assertTrue(self.app.projectDirty)
        self.app.loadProject(str(other_path))
        self.app.autosave_timer.stop()

        self.assertEqual(
            load_project(project_path)["segments"][0]["text"],
            "saved before project switch",
        )
        self.assertEqual(Path(self.app.projectPath).resolve(), other_path.resolve())
        self.assertEqual(self.app.subtitleSegments[0]["text"], "other project")

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

    def test_video_only_external_mixer_settings_survive_channel_updates_and_reload(self) -> None:
        project_path, _video, audio = self._make_project()
        second_audio = self.root / "2-bob.flac"
        second_audio.write_bytes(b"audio")
        project = load_project(project_path)
        project["audio_sources"] = [
            {"path": str(audio), "track_key": "craig:Alice", "file_name": audio.name},
            {"path": str(second_audio), "track_key": "craig:Bob", "file_name": second_audio.name},
        ]
        save_project(project_path, project)

        self.app._audio_tracks = [{"selector": "", "label": "音声トラックなし"}]
        self.assertTrue(self.app._load_project_path(project_path, update_sources=False))
        self.assertEqual([channel["kind"] for channel in self.app.audioMixerChannels], ["external", "external"])

        self.app.updateAudioMixChannel(
            0,
            {"enabled": True, "muted": True, "solo": False, "volume_percent": 42},
        )
        self.app.updateAudioMixChannel(
            1,
            {"enabled": True, "muted": False, "solo": True, "volume_percent": 157},
        )
        self.assertTrue(self.app.saveProject())

        self.assertTrue(self.app._load_project_path(project_path, update_sources=False))
        channels = self.app.audioMixerChannels
        self.assertEqual(
            [
                (
                    channel["volume_percent"],
                    channel["muted"],
                    channel["solo"],
                    channel["enabled"],
                )
                for channel in channels
            ],
            [(42.0, True, False, True), (157.0, False, True, True)],
        )

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
        generation = self.app.audioPreviewGeneration

        with patch("src.gui.clear_audio_preview_cache") as clear_cache:
            clear_cache.return_value = (0, 0)
            self.app.clearAudioPreviewCache()

        clear_cache.assert_called_once_with(self.app.audio_preview_cache_root)
        self.assertFalse(self.app._audio_preview_cache_paths)
        self.assertFalse(self.app.audioPreviewPreparing)
        self.assertEqual(self.app.audioPreviewGeneration, generation + 1)

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
            "manual f\nirst\nmanual\nsecond",
        )

        self.app.updateSegment(0, {"text": "manual first\nmanual second"})
        saved = self.app.subtitleSegments[0]
        preview = self.app.activeSubtitleSegments(1.0)[0]
        self.assertEqual(saved["text"], "manual first\nmanual second")
        self.assertEqual(preview["preview_text"], "manual f\nirst\nmanual\nsecond")
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
        project["render_settings"] = {
            "audio_normalize": False,
            "audio_target_lufs": -20,
            "cut_no_speech": True,
            "no_speech_min_seconds": 1.7,
            "speech_padding_seconds": 0.25,
            "speech_threshold_db": "-35dB",
            "speech_min_clip_seconds": 0.4,
            "video_codec": "h264_nvenc",
            "nvenc_cq": 22,
            "x264_crf": 17,
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
        self.assertFalse(self.app.settings["audio_normalize"])
        self.assertEqual(self.app.settings["audio_target_lufs"], -20)
        self.assertTrue(self.app.settings["cut_no_speech"])
        self.assertEqual(self.app.settings["no_speech_min_seconds"], 1.7)
        self.assertEqual(self.app.settings["speech_padding_seconds"], 0.25)
        self.assertEqual(self.app.settings["speech_threshold_db"], "-35dB")
        self.assertEqual(self.app.settings["speech_min_clip_seconds"], 0.4)
        self.assertEqual(self.app.settings["video_codec"], "h264_nvenc")
        self.assertEqual(self.app.settings["nvenc_cq"], 22)
        self.assertEqual(self.app.settings["x264_crf"], 17)
        self.assertFalse(self.app.projectDirty)

        _, window = self._load_qml()
        self.assertEqual(self._quick_item(window, "fontSizeSpin").property("value"), 200)
        self.assertEqual(self._quick_item(window, "outlineColorButton").property("colorValue"), "#123456")
        self.assertEqual(self._quick_item(window, "outlineThicknessSpin").property("value"), 6)
        self.assertEqual(self._quick_item(window, "volumeScaleSpin").property("value"), 35)
        self.assertEqual(self._quick_item(window, "qualitySpin").property("value"), 22)
        self.assertFalse(self._quick_item(window, "normalizeSwitch").property("checked"))
        self.assertTrue(self._quick_item(window, "silenceSwitch").property("checked"))
        self.assertEqual(self._quick_item(window, "silenceField").property("text"), "1.7")
        self.assertEqual(self._quick_item(window, "speechPaddingField").property("text"), "0.25")
        self.assertEqual(self._quick_item(window, "speechThresholdField").property("text"), "-35")
        self.assertEqual(self._quick_item(window, "lufsField").property("text"), "-20")
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

    def test_process_failure_detail_uses_worker_output_not_newer_system_status(self) -> None:
        self.app._active_job = "render"
        self.app._running = True
        self.app._cancel_requested = False

        with (
            patch.object(
                self.app.process,
                "readAllStandardOutput",
                return_value=b"Starting WhisperX\ninput audio became unavailable\n",
            ),
            patch.object(self.app.process, "processId", return_value=42),
        ):
            self.app._process_finished(23, QProcess.ExitStatus.NormalExit)

        self.assertEqual(self.app.stage, "ERROR")
        self.assertIn("input audio became unavailable", self.app.status)
        self.assertNotIn("文字起こししています", self.app.status)

    def test_final_progress_event_does_not_mask_process_error_or_cancel(self) -> None:
        final_events = "\n".join(
            f'PROGRESS_EVENT {{"job":"render","step":"{step}","phase":"complete","progress":1.0}}'
            for step in ("prepare", "subtitle", "audio", "encode", "finalize")
        )

        self.app._active_job = "render"
        self.app._running = True
        self.app._cancel_requested = False
        self.app._processing_progress.start("render")
        self.app._update_stage(final_events)
        self.assertLess(self.app.progressPercent, 100)
        self.app._process_finished(7, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.app.progressState, "error")
        self.assertLess(self.app.progressPercent, 100)
        self.assertEqual(self.app.progressSteps[-1]["state"], "error")

        self.app._active_job = "render"
        self.app._running = True
        self.app._cancel_requested = True
        self.app._processing_progress.start("render")
        self.app._update_stage(final_events)
        self.assertLess(self.app.progressPercent, 100)
        self.app._process_finished(1, QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.app.progressState, "cancelled")
        self.assertLess(self.app.progressPercent, 100)
        self.assertEqual(self.app.progressSteps[-1]["state"], "cancelled")

    def test_trackerless_update_reaches_complete_progress_on_success(self) -> None:
        self.app._active_job = "update"
        self.app._running = True
        self.app._progress = 0.02
        self.app._processing_progress.start("update")

        self.app._process_finished(0, QProcess.ExitStatus.NormalExit)

        self.assertEqual(self.app.progress, 1.0)
        self.assertEqual(self.app.progressState, "completed")

    def test_processing_progress_gui_exposes_job_sequence_and_terminal_states(self) -> None:
        for job, step in (("transcribe", "alignment"), ("render", "encode"), ("render_short", "clips")):
            with self.subTest(job=job), patch.object(self.app, "_start_process"):
                self.app._start_command(["worker"], job, "処理を開始しています")
                self.assertTrue(self.app.progressVisible)
                self.assertEqual(self.app.activeJob, job)
                self.app._update_stage(
                    f'PROGRESS_EVENT {{"job":"{job}","step":"{step}","phase":"start","progress":0.25}}'
                )
                self.assertEqual(self.app.progressCurrentStep, step)
                self.assertGreaterEqual(self.app.progressPercent, 0)
                self.app._finish_processing_progress("completed")
                self.assertEqual(self.app.progressPercent, 100)

                self.app._processing_progress.start(job)
                self.app._update_stage(
                    f'PROGRESS_EVENT {{"job":"{job}","step":"{step}","phase":"start","progress":0.25}}'
                )
                before = self.app.progressPercent
                self.app._finish_processing_progress("cancelled")
                self.assertEqual(self.app.progressPercent, before)
                self.assertEqual(self.app.progressState, "cancelled")

                self.app._processing_progress.start(job)
                self.app._update_stage(
                    f'PROGRESS_EVENT {{"job":"{job}","step":"{step}","phase":"start","progress":0.25}}'
                )
                before = self.app.progressPercent
                self.app._finish_processing_progress("error")
                self.assertEqual(self.app.progressPercent, before)
                self.assertEqual(self.app.progressState, "error")

    def test_processing_progress_uses_tracker_value_and_short_output_duration(self) -> None:
        self.app._processing_machine_event_seen = False
        self.app._progress = 0.0
        self.app._processing_progress.start("transcribe")
        self.app._update_stage("[subtitle_workflow] Building waveform for 1-alice.flac")
        self.assertEqual(self.app.progress, 0.0)

        self.app._processing_progress.start("transcribe")
        self.app._update_stage(
            'PROGRESS_EVENT {"job":"transcribe","step":"alignment","phase":"start","progress":0.5}'
        )
        self.assertEqual(self.app.progress, self.app._processing_progress.value)
        self.assertEqual(self.app.progressPercent, round(self.app.progress * 100))

        self.app._update_stage("[subtitle_workflow] Refining merged subtitle segments")
        self.assertEqual(self.app.progress, self.app._processing_progress.value)
        self.assertEqual(self.app.progressPercent, round(self.app.progress * 100))

        self.app._processing_progress.start("render_short")
        self.app._update_stage(
            'PROGRESS_EVENT {"job":"render_short","step":"encode","phase":"start","progress":0.0}'
        )
        self.app._update_stage(
            '\n'.join(
                (
                    'PROGRESS_EVENT {"job":"render_short","step":"encode","phase":"metadata","progress":0.0,"duration":30.0}',
                    "Duration: 02:00:00.00, start: 0.000000, bitrate: 100 kb/s",
                    "frame=10 time=00:00:15.00 speed=1x",
                )
            )
        )
        self.assertEqual(self.app._ffmpeg_duration_seconds, 30.0)
        encode_step = next(step for step in self.app.progressSteps if step["id"] == "encode")
        self.assertAlmostEqual(encode_step["progress"], 0.5)

    def test_processing_progress_ignores_legacy_encode_marker_and_refreshes_cut_duration(self) -> None:
        self.app._processing_machine_event_seen = False
        self.app._ffmpeg_duration_seconds = 0.0
        self.app._ffmpeg_duration_from_event = False
        self.app._processing_progress.start("render")
        self.app._update_stage(
            "\n".join(
                (
                    'PROGRESS_EVENT {"job":"render","step":"prepare","phase":"complete","progress":1.0}',
                    'PROGRESS_EVENT {"job":"render","step":"subtitle","phase":"complete","progress":1.0}',
                    'PROGRESS_EVENT {"job":"render","step":"audio","phase":"complete","progress":1.0}',
                )
            )
        )
        before_encode = self.app.progress
        self.app._update_stage("[subtitle_workflow] Rendering edited subtitles to output.mp4")
        self.assertEqual(self.app.progress, before_encode)

        self.app._update_stage(
            "\n".join(
                (
                    'PROGRESS_EVENT {"job":"render","step":"encode","phase":"start","progress":0.0}',
                    "Duration: 01:00:00.00, start: 0.000000, bitrate: 100 kb/s",
                )
            )
        )
        self.assertEqual(self.app._ffmpeg_duration_seconds, 3600.0)
        self.app._update_stage(
            'PROGRESS_EVENT {"job":"render","step":"encode","phase":"start","progress":0.0}'
        )
        self.assertEqual(self.app._ffmpeg_duration_seconds, 0.0)
        self.app._update_stage("Duration: 00:20:00.00, start: 0.000000, bitrate: 100 kb/s")
        self.assertEqual(self.app._ffmpeg_duration_seconds, 1200.0)

    def test_startup_system_log_contains_runtime_dependencies_config_and_completion(self) -> None:
        startup_log = self._startup_log_text

        for marker in (
            "[startup] アプリケーションの起動を開始しました",
            "[startup] アプリケーション: version=",
            "[startup] パス: executable=",
            "[runtime] 実行環境: python=",
            "[runtime] 依存関係: ffmpeg=",
            "[config] 設定: source=",
            "[startup] バックエンドの初期化が完了しました",
        ):
            self.assertIn(marker, startup_log)

    def test_starting_process_preserves_existing_system_log(self) -> None:
        self.app._record_log(
            "起動診断を保持",
            component="startup",
            stage="STARTUP",
        )

        with patch.object(self.app, "_start_process") as start:
            self.app._start_command(
                [sys.executable, "-m", "src.subtitle_workflow", "render"],
                "render",
                "動画を書き出しています",
            )

        start.assert_called_once()
        self.assertIn("起動診断を保持", self.app.logText)
        self.assertIn("src.subtitle_workflow", self.app.logText)
        self.assertIn("動画を書き出しています", self.app.logText)

    def test_backend_pins_startup_log_during_preserved_gui_status_flood(self) -> None:
        original_limit = self.app._application_logger.max_memory_chars
        try:
            self.app._application_logger.max_memory_chars = 1_000
            self.app._record_log(
                "startup sentinel",
                component="runtime",
                stage="STARTUP",
            )
            for index in range(80):
                self.app._record_log(
                    f"GUI state {index:03d} " + ("x" * 80),
                    component="gui",
                    stage="READY",
                )

            self.assertIn("startup sentinel", self.app.logText)
            self.assertNotIn("GUI state 000", self.app.logText)
            self.assertIn("GUI state 079", self.app.logText)
        finally:
            self.app._application_logger.max_memory_chars = original_limit

    def test_qml_system_log_panel_displays_startup_entries(self) -> None:
        self.app._record_log(
            "起動時システムログを表示",
            component="startup",
            stage="STARTUP",
        )
        _, window = self._load_qml()

        text_area = self._quick_item(window, "applicationLogTextArea")
        self.assertIn("起動時システムログを表示", text_area.property("text"))
        self._click(window, self._quick_item(window, "applicationLogToggleButton"))
        self.assertTrue(self._quick_item(window, "applicationLogPanel").property("expanded"))

    def test_qml_system_log_panel_scrolls_to_the_latest_entry(self) -> None:
        for index in range(250):
            self.app._record_log(
                f"system-log-{index:03d} " + ("x" * 80),
                component="render",
                preserve_in_memory=False,
            )
        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "applicationLogToggleButton"))

        scroll_view = self._quick_item(window, "applicationLogScrollView")
        scroll_bar = self._quick_item(window, "applicationLogVerticalScrollBar")
        text_area = self._quick_item(window, "applicationLogTextArea")
        flickable = scroll_view.property("contentItem")
        self.assertIsNotNone(flickable)
        self.assertIn("system-log-249", text_area.property("text"))

        content_height = float(flickable.property("contentHeight"))
        viewport_height = float(flickable.property("height"))
        self.assertGreater(content_height, viewport_height)
        self.assertTrue(scroll_bar.isVisible())
        self.assertLess(float(scroll_bar.property("size")), 1.0)

        max_content_y = content_height - viewport_height
        flickable.setProperty("contentY", max_content_y)
        self.app.processEvents()
        self.assertAlmostEqual(float(flickable.property("contentY")), max_content_y, delta=1.0)
        self.assertLessEqual(
            float(text_area.property("contentHeight")) - float(flickable.property("contentY")),
            viewport_height + 2.0,
        )

    def test_status_dependency_and_codex_system_logs_are_recorded_and_redacted(self) -> None:
        secret = "do-not-store"
        self.app._set_status(f"起動診断 token={secret}", "ERROR")
        self.assertIn("[ERROR] [gui]", self.app.logText)
        self.assertNotIn(secret, self.app.logText)

        with patch(
            "src.gui_base.check_runtime_dependencies",
            return_value=RuntimeDependencyStatus(
                ffmpeg=True,
                ffprobe=True,
                whisperx=True,
                cuda=True,
                nvenc=True,
            ),
        ):
            self.app.refreshDependencies()
        self.assertIn("[runtime] 依存関係:", self.app.logText)

        runtime = CodexRuntimeInfo(
            available=True,
            executable="codex",
            version="codex-cli 0.99.0",
            distribution="git",
        )
        with patch("src.gui.detect_codex", return_value=runtime):
            client = self.app._create_codex_chat_client()
        self.assertIsNotNone(client.log_callback)
        log_thread = threading.Thread(
            target=client.log_callback,
            args=(f"request initialize token={secret}",),
        )
        log_thread.start()
        log_thread.join(timeout=1)
        self.assertFalse(log_thread.is_alive())
        QTest.qWait(20)
        self.app.processEvents()

        self.app._on_codex_chat_state(
            CodexChatSnapshot(
                connection_state="error",
                auth_state="error",
                chat_state="disconnected",
                login_url="https://example.invalid/private-login",
                error=f"Codexへ接続できません token={secret}",
            )
        )
        self.assertIn("Codex CLI検出成功", self.app.logText)
        self.assertIn("request initialize", self.app.logText)
        self.assertIn("Codex状態: connection=error", self.app.logText)
        self.assertNotIn(secret, self.app.logText)
        self.assertNotIn("private-login", self.app.logText)

    def test_error_copy_preserves_failure_after_settings_save_and_drains_output(self) -> None:
        output = self.root / "output"
        transcript_directory = output / "transcripts"
        transcript_directory.mkdir(parents=True)
        (transcript_directory / "speaker.whisperx.log").write_text(
            "WhisperX traceback: CUDA out of memory\n",
            encoding="utf-8",
        )
        self.app._source_selection = SourceSelection(output_dir=str(output))
        self.app._active_job = "transcribe"
        self.app._running = True
        _, window = self._load_qml()

        with (
            patch("src.gui.runtime_diagnostic_info", return_value={"pytorch": "2.8.0+cu128"}),
            patch.object(
                self.app.process,
                "readAllStandardOutput",
                return_value=b"final ffmpeg stderr: encoder failed\n",
            ),
            patch.object(self.app.process, "processId", return_value=42),
        ):
            self.app._process_finished(7, QProcess.ExitStatus.NormalExit)

        self.app.saveSettings(self.app.settings)
        self.app.processEvents()
        self.assertEqual(self.app.stage, "SAVED")
        self.assertTrue(self.app.hasLastProcessDiagnostic)
        self.assertTrue(self._quick_item(window, "copyErrorLogsButton").isVisible())

        self.app.copyErrorLogsToClipboard()
        error_diagnostic = self.app.clipboard().text()
        self.assertIn("job: transcribe", error_diagnostic)
        self.assertIn("工程: ERROR", error_diagnostic)
        self.assertIn("結果: 異常終了 (failed)", error_diagnostic)
        self.assertIn("終了コード: 7", error_diagnostic)
        self.assertIn("final ffmpeg stderr: encoder failed", error_diagnostic)
        self.assertIn("WhisperX traceback: CUDA out of memory", error_diagnostic)
        self.assertNotIn("工程: SAVED", error_diagnostic)

        with patch("src.gui.runtime_diagnostic_info", return_value={}):
            self.app.copyLogsToClipboard()
        current_diagnostic = self.app.clipboard().text()
        self.assertIn("工程: SAVED", current_diagnostic)
        self.assertIn("status: GUI設定を保存しました", current_diagnostic)

    def test_cancelled_process_has_a_distinct_diagnostic_result(self) -> None:
        self.app._active_job = "render"
        self.app._running = True
        self.app._cancel_requested = True
        _, window = self._load_qml()

        with (
            patch("src.gui.runtime_diagnostic_info", return_value={}),
            patch.object(self.app.process, "readAllStandardOutput", return_value=b""),
        ):
            self.app._process_finished(1, QProcess.ExitStatus.NormalExit)

        self.app.processEvents()
        self.assertTrue(self.app.hasLastProcessDiagnostic)
        self.assertTrue(self._quick_item(window, "copyErrorLogsButton").isVisible())
        self.app.copyErrorLogsToClipboard()
        diagnostic = self.app.clipboard().text()
        self.assertIn("工程: CANCELLED", diagnostic)
        self.assertIn("status: 処理を停止しました", diagnostic)
        self.assertIn("結果: キャンセル (cancelled)", diagnostic)
        self.assertIn("終了コード: 1", diagnostic)

    def test_failed_process_start_captures_qprocess_error(self) -> None:
        self.app._active_job = "transcribe"
        self.app._running = False
        generated_path = self.root / ".failed-transcription.subtitle-project.json"
        generated_path.write_text("temporary", encoding="utf-8")
        self.app._transcription_merge_mode = "merge"
        self.app._transcription_preserved_project = {"segments": []}
        self.app._transcription_preserved_project_path = str(self.root / "preserved.subtitle-project.json")
        self.app._transcription_generated_project_path = str(generated_path)

        with (
            patch("src.gui.runtime_diagnostic_info", return_value={}),
            patch.object(self.app.process, "readAllStandardOutput", return_value=b"launcher stderr\n"),
            patch.object(self.app.process, "errorString", return_value="プロセスを開始できません"),
            patch.object(
                self.app.process,
                "state",
                return_value=QProcess.ProcessState.NotRunning,
            ),
            patch.object(self.app.process, "processId", return_value=0),
        ):
            self.app._process_error(QProcess.ProcessError.FailedToStart)

        self.app.copyErrorLogsToClipboard()
        diagnostic = self.app.clipboard().text()
        self.assertIn("job: transcribe", diagnostic)
        self.assertIn("結果: 異常終了 (failed)", diagnostic)
        self.assertIn("QProcessエラー: プロセスを開始できません", diagnostic)
        self.assertIn("launcher stderr", diagnostic)
        self.assertEqual(self.app.activeJob, "")
        self.assertEqual(self.app._transcription_merge_mode, "")
        self.assertIsNone(self.app._transcription_preserved_project)
        self.assertEqual(self.app._transcription_preserved_project_path, "")
        self.assertFalse(generated_path.exists())

    def test_starting_a_new_process_discards_the_previous_error_snapshot(self) -> None:
        self.app._active_job = "render"
        self.app._running = True
        with (
            patch("src.gui.runtime_diagnostic_info", return_value={}),
            patch.object(self.app.process, "readAllStandardOutput", return_value=b"failed\n"),
        ):
            self.app._process_finished(9, QProcess.ExitStatus.NormalExit)
        self.assertIsNotNone(self.app._last_process_diagnostic)

        with patch.object(self.app, "_start_process"):
            self.app._start_command(["python", "worker.py"], "render", "開始しています")

        self.assertIsNone(self.app._last_process_diagnostic)
        self.assertFalse(self.app.hasLastProcessDiagnostic)

    def test_cancel_fallback_does_not_kill_replacement_process(self) -> None:
        with (
            patch.object(self.app.process, "processId", return_value=222),
            patch.object(self.app.process, "kill") as kill,
        ):
            self.app._kill_if_running(111)

        kill.assert_not_called()

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
        self.assertTrue(transcribe.isVisible())
        self.assertIn("追加 / 更新", transcribe.property("text"))
        self.assertFalse(transcribe.isEnabled())
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
        self.assertEqual(caption.property("text"), "manual f\nirst\nmanual\nsecond")

        self._click(window, self._quick_item(window, "saveProjectButton"))
        self.assertEqual(self.app.subtitleSegments[0]["text"], "manual first\nmanual second")

    def test_qml_settings_round_trip_and_expanded_popup_fit(self) -> None:
        _, window = self._load_qml()
        self._load_project()
        panel = self._quick_item(window, "advancedSettingsPanel")
        toggle = self._quick_item(window, "settingsToggleButton")
        self.assertFalse(panel.isVisible())

        self._click(window, toggle)
        self.assertTrue(panel.isVisible())
        actions = self._quick_item(window, "workflowActions")
        self.assertLessEqual(actions.y() + actions.height(), actions.parentItem().height() + 1)

        self._click(window, toggle)
        self.assertFalse(panel.isVisible())
        self.assertFalse(window.property("settingsExpanded"))
        self._click(window, toggle)
        self.assertTrue(panel.isVisible())

        font_size = self._quick_item(window, "fontSizeSpin")
        self.assertEqual(font_size.property("to"), 900)
        font_size.setProperty("value", 900)
        self.assertEqual(window.property("subtitleFontSizePercent"), 900)
        self.assertEqual(window.property("selectedSubtitleFontSize"), 450)
        self._quick_item(window, "outlineColorButton").setProperty("colorValue", "#456789")
        self._quick_item(window, "outlineThicknessSpin").setProperty("value", 9)
        self._quick_item(window, "volumeScaleSpin").setProperty("value", 30)
        self.assertEqual(window.currentSettings().toVariant()["subtitle_font_size"], 450)
        self._click(window, self._quick_item(window, "settingsPopupSaveButton"))
        self.assertEqual(self.app.settings["subtitle_font_size"], 450)
        self.assertEqual(self.app.settings["subtitle_outline_color"], "#456789")
        self.assertEqual(self.app.settings["subtitle_outline_thickness"], 9)
        self.assertEqual(self.app.settings["subtitle_volume_scale_percent"], 30)

        self._click(window, self._quick_item(window, "settingsPopupCloseButton"))
        self.assertFalse(window.property("settingsExpanded"))

    def test_qml_settings_popup_keeps_actions_visible_and_bottom_settings_scrollable(self) -> None:
        _, window = self._load_qml()
        toggle = self._quick_item(window, "settingsToggleButton")
        self._click(window, toggle)

        panel = self._quick_item(window, "advancedSettingsPanel")
        scroll_view = self._quick_item(window, "advancedSettingsScrollView")
        scroll_content = self._quick_item(window, "advancedSettingsContent")
        scroll_bar = self._quick_item(window, "advancedSettingsVerticalScrollBar")
        save_button = self._quick_item(window, "settingsPopupSaveButton")
        close_button = self._quick_item(window, "settingsPopupCloseButton")
        bottom_field = self._quick_item(window, "speechThresholdField")
        flickable = scroll_view.property("contentItem")
        self.assertIsNotNone(flickable)

        for width, height in ((1220, 760), (1520, 940)):
            self.gui.resize(window, width, height)
            self.gui.wait_until(
                lambda: scroll_view.height() > 0,
                description="advanced settings scroll view layout",
            )

            self._assert_quick_item_within(window.contentItem(), panel)
            self._assert_quick_item_within(panel, save_button)
            self._assert_quick_item_within(panel, close_button)
            self.assertGreater(scroll_view.height(), 0)
            self.assertGreater(scroll_content.property("implicitHeight"), scroll_view.height())
            self.assertTrue(scroll_bar.isVisible())
            self.assertLess(float(scroll_bar.property("size")), 1.0)

            max_content_y = max(
                0.0,
                float(flickable.property("contentHeight")) - float(flickable.property("height")),
            )
            flickable.setProperty("contentY", max_content_y)
            self.app.processEvents()
            self._assert_quick_item_within(scroll_view, bottom_field)
            self._assert_quick_item_within(panel, save_button)
            self._assert_quick_item_within(panel, close_button)
            flickable.setProperty("contentY", 0)

    def test_qml_settings_popup_closes_on_escape_and_screen_navigation(self) -> None:
        self._load_project()
        _, window = self._load_qml()
        panel = self._quick_item(window, "advancedSettingsPanel")
        toggle = self._quick_item(window, "settingsToggleButton")

        self._click(window, toggle)
        self.assertTrue(panel.isVisible())
        self.gui.key_click(window, Qt.Key.Key_Escape)
        self.gui.wait_until(
            lambda: not panel.isVisible(),
            description="settings popup to close after Escape",
        )
        self.assertFalse(panel.isVisible())
        self.assertFalse(window.property("settingsExpanded"))

        self._click(window, toggle)
        self.assertTrue(panel.isVisible())
        self._click(window, self._quick_item(window, "editSubtitlesButton"))
        editor_page = self._quick_item(window, "editorPage")
        self.gui.wait_until(
            lambda: editor_page.isVisible() and not panel.isVisible(),
            description="editor navigation and settings popup close",
        )

        self.assertTrue(editor_page.isVisible())
        self.assertFalse(panel.isVisible())
        self.assertFalse(window.property("settingsExpanded"))

    def test_qml_workflow_layout_fits_supported_window_sizes(self) -> None:
        self._load_project()
        _, window = self._load_qml()
        action_bar = self._quick_item(window, "contextActionBar")
        video_panel = self._quick_item(window, "mainVideoPanel")
        log_panel = self._quick_item(window, "applicationLogPanel")
        central_column = action_bar.parentItem()

        for width, height in ((1220, 760), (1520, 940)):
            self.gui.resize(window, width, height)

            self.assertGreaterEqual(window.width(), width)
            self.assertGreaterEqual(window.height(), height)
            for item in (action_bar, video_panel, log_panel):
                self.assertGreater(item.width(), 0)
                self.assertGreater(item.height(), 0)
                self.assertGreaterEqual(item.x(), -1)
                self.assertLessEqual(item.x() + item.width(), central_column.width() + 1)
                self.assertGreaterEqual(item.y(), -1)
                self.assertLessEqual(item.y() + item.height(), central_column.height() + 1)

            self.assertLessEqual(action_bar.y() + action_bar.height(), video_panel.y() + 1)
            self.assertLessEqual(video_panel.y() + video_panel.height(), log_panel.y() + 1)

        self.gui.resize(window, 1220, 760)
        log_toggle = self._quick_item(window, "applicationLogToggleButton")
        self._click(window, log_toggle)
        self.gui.wait_until(
            lambda: (
                bool(log_panel.property("expanded"))
                and video_panel.height() > 0
                and log_panel.y() + log_panel.height() <= central_column.height() + 1
            ),
            description="expanded application log layout",
        )

        self.assertTrue(log_panel.property("expanded"))
        self.assertGreater(log_panel.height(), 0)
        self.assertGreater(video_panel.height(), 0)
        self.assertLessEqual(action_bar.y() + action_bar.height(), video_panel.y() + 1)
        self.assertLessEqual(video_panel.y() + video_panel.height(), log_panel.y() + 1)
        self.assertLessEqual(log_panel.y() + log_panel.height(), central_column.height() + 1)

        self._click(window, log_toggle)
        self.gui.wait_until(
            lambda: not bool(log_panel.property("expanded")),
            description="collapsed application log",
        )
        self.app._set_status("GUI layout error", "ERROR")
        self.gui.wait_until(
            lambda: (
                bool(log_panel.property("expanded"))
                and video_panel.height() > 0
                and log_panel.y() + log_panel.height() <= central_column.height() + 1
            ),
            description="application log automatically expanded for an error",
        )

        self.assertTrue(log_panel.property("expanded"))
        self.assertGreater(log_panel.height(), 0)
        self.assertGreater(video_panel.height(), 0)
        self.assertLessEqual(log_panel.y() + log_panel.height(), central_column.height() + 1)

    def test_common_editor_workspace_switches_modes_without_losing_playhead(self) -> None:
        path, _, _ = self._make_project()
        self.assertTrue(self.app._load_project_path(path, update_sources=True))
        self._prime_audio_preview_cache()
        self.app._dependencies = RuntimeDependencyStatus(
            ffmpeg=True,
            ffprobe=True,
            whisperx=False,
            cuda=False,
        )
        self.app.dependenciesChanged.emit()
        _, window = self._load_qml()
        self.gui.resize(window, 1220, 760)

        main = self._quick_item(window, "mainWorkspace")
        rail = self._quick_item(window, "editorModeRail")
        video = self._quick_item(window, "mainVideoPanel")
        editor_slot = self._quick_item(window, "modeEditorSlot")
        settings_slot = self._quick_item(window, "modeSettingsSlot")
        editor_loader = self._quick_item(window, "modeEditorContentLoader")
        settings_loader = self._quick_item(window, "modeSettingsContentLoader")
        editor_fallback = self._quick_item(window, "modeEditorFallback")
        settings_fallback = self._quick_item(window, "modeSettingsFallback")
        audio_bridge = self._quick_item(window, "workspaceAudioPreviewBridge")
        main_audio_output = window.findChild(QObject, "mainWorkspaceAudioOutput")
        self.assertIsNotNone(main_audio_output)
        subtitle_button = self._quick_item(window, "editorModeButton-subtitle")
        cut_button = self._quick_item(window, "editorModeButton-cut")
        audio_button = self._quick_item(window, "editorModeButton-audio")

        self.assertTrue(main.isVisible())
        self.assertTrue(rail.isVisible())
        self.assertTrue(subtitle_button.isEnabled())
        self.assertFalse(cut_button.isEnabled())
        self.assertTrue(audio_button.isEnabled())
        self.assertTrue(editor_loader.property("active"))
        self.assertTrue(settings_loader.property("active"))
        self.assertFalse(editor_fallback.isVisible())
        self.assertFalse(settings_fallback.isVisible())
        subtitle_editor = self._quick_item(window, "workspaceSubtitleEditor")
        self.assertTrue(subtitle_editor.isVisible())
        subtitle_timeline = self._quick_visual_item(
            subtitle_editor,
            "workspaceSubtitleTimeline",
        )
        subtitle_timeline.setProperty("viewportX", 180.0)
        self.app.processEvents()
        self.assertTrue(self._quick_item(window, "workspaceSubtitleSettings").isVisible())
        self.assertFalse(audio_bridge.property("active"))
        self.assertFalse(audio_bridge.property("prepared"))
        self.assertTrue(self.app.editorModeCapabilities["canPreview"])
        self.assertTrue(self.app.editorModeCapabilities["canEditSubtitles"])
        self.assertFalse(self.app.editorModeCapabilities["canCut"])
        self.assertTrue(self.app.editorModeCapabilities["canMixAudio"])

        class OffsetTimeMapping:
            @staticmethod
            def source_to_output(position_ms: int) -> int:
                return max(0, position_ms - 1_000)

            @staticmethod
            def output_to_source(position_ms: int) -> int:
                return position_ms + 1_000

        self.app.set_editor_time_mapping(OffsetTimeMapping())
        window.seekSharedPlayer(12_345, "output")
        self.app.processEvents()
        self.assertEqual(
            self.app.editorPlayhead,
            {"basis": "output", "sourcePositionMs": 13_345, "outputPositionMs": 12_345},
        )
        self._click(window, audio_button)
        self.assertEqual(self.app.currentEditMode, "audio")
        self.assertEqual(
            self.app.editorPlayhead,
            {"basis": "output", "sourcePositionMs": 13_345, "outputPositionMs": 12_345},
        )
        self.assertTrue(main.isVisible())
        self.assertFalse(self._quick_item(window, "mixerPage").isVisible())
        self.assertTrue(self._quick_item(window, "workspaceAudioEditor").isVisible())
        audio_timeline = self._quick_item(window, "workspaceAudioTimeline")
        audio_timeline.setProperty("viewportX", 260.0)
        self.app.processEvents()
        audio_settings = self._quick_item(window, "workspaceAudioSettings")
        self.assertTrue(audio_settings.isVisible())
        self.assertTrue(audio_bridge.property("active"))
        self.assertTrue(audio_bridge.property("prepared"))
        self.assertTrue(main_audio_output.property("muted"))
        first_channel = self.app.audioMixerChannels[0]
        mute_button = self._quick_visual_item(audio_settings, "workspaceAudioMuteButton")
        self._click(window, mute_button)
        self.assertNotEqual(self.app.audioMixerChannels[0]["muted"], first_channel["muted"])
        self.assertEqual(self.app.editorPlayhead["outputPositionMs"], 12_345)
        preview_channel_id = self.app.audioMixerPreviewChannels[0]["id"]
        preview_player = window.findChild(
            QObject,
            f"workspaceAudioPreviewPlayer-{preview_channel_id}",
        )
        self.assertIsNotNone(preview_player)
        self._click(window, subtitle_button)
        self.assertEqual(self.app.currentEditMode, "subtitle")
        self.assertEqual(
            self.app.editorPlayhead,
            {"basis": "output", "sourcePositionMs": 13_345, "outputPositionMs": 12_345},
        )
        subtitle_editor = self._quick_item(window, "workspaceSubtitleEditor")
        self.assertTrue(subtitle_editor.isVisible())
        self.assertAlmostEqual(
            float(
                self._quick_visual_item(
                    subtitle_editor,
                    "workspaceSubtitleTimeline",
                ).property("viewportX")
            ),
            180.0,
            delta=1.0,
        )
        self.assertFalse(audio_bridge.property("active"))
        self.assertTrue(audio_bridge.property("prepared"))
        self.assertFalse(main_audio_output.property("muted"))
        self.assertIs(
            window.findChild(QObject, f"workspaceAudioPreviewPlayer-{preview_channel_id}"),
            preview_player,
        )

        self._click(window, audio_button)
        self.assertTrue(audio_bridge.property("active"))
        self.assertAlmostEqual(
            float(self._quick_item(window, "workspaceAudioTimeline").property("viewportX")),
            260.0,
            delta=1.0,
        )
        self.assertIs(
            window.findChild(QObject, f"workspaceAudioPreviewPlayer-{preview_channel_id}"),
            preview_player,
        )
        for channel_index in range(len(self.app.audioMixerChannels)):
            self.app.updateAudioMixChannel(channel_index, {"enabled": False})
        self.app.processEvents()
        self.assertFalse(audio_bridge.property("previewReady"))
        self.assertTrue(main_audio_output.property("muted"))
        self._click(window, subtitle_button)
        self.assertFalse(main_audio_output.property("muted"))

        self.app.set_cut_editor_available(True)
        self.app.processEvents()
        self.assertTrue(cut_button.isEnabled())
        self._click(window, cut_button)
        self.assertEqual(self.app.currentEditMode, "cut")
        self.assertEqual(self.app.editorPlayhead["outputPositionMs"], 12_345)
        self.assertTrue(main.isVisible())
        self.assertFalse(editor_loader.property("active"))
        self.assertFalse(settings_loader.property("active"))
        self.assertTrue(editor_fallback.isVisible())
        self.assertTrue(settings_fallback.isVisible())
        self.app.set_cut_editor_available(False)
        self.app.processEvents()
        self.assertEqual(self.app.currentEditMode, "subtitle")
        self.assertFalse(cut_button.isEnabled())
        self.assertTrue(editor_loader.property("active"))
        self.assertTrue(settings_loader.property("active"))

        for item in (rail, video, editor_slot, settings_slot):
            self.assertGreater(item.width(), 0, item.objectName())
            self.assertGreater(item.height(), 0, item.objectName())
            self._assert_quick_item_within(main, item)
        self.assertLessEqual(video.y() + video.height(), editor_slot.y() + 1)
        self.assertLessEqual(rail.x() + rail.width(), video.parentItem().x() + 1)
        self.assertLessEqual(video.parentItem().x() + video.parentItem().width(), settings_slot.x() + 1)

        self.app._project["audio_mix"]["channels"] = []
        self.app.projectDataChanged.emit()
        self.app.processEvents()
        self.assertFalse(self.app.editorModeCapabilities["canMixAudio"])
        self.assertFalse(audio_button.isEnabled())
        self.assertTrue(subtitle_button.isEnabled())

    def test_workspace_subtitle_mode_adds_first_caption_at_shared_playhead(self) -> None:
        path, _, _ = self._make_project()
        self.assertTrue(self.app._load_project_path(path, update_sources=True))
        assert self.app._project is not None
        self.app._project["segments"] = []
        self.app._selected_segment_index = -1
        self.app._sync_subtitle_model()
        self.app.segmentsChanged.emit()
        self.app.selectionChanged.emit()
        _, window = self._load_qml()

        self.assertEqual(self.app.segmentCount, 0)
        self.assertTrue(self._quick_item(window, "editorModeButton-audio").isEnabled())
        self.app.setEditorPlayhead(2_500, "source")
        subtitle_editor = self._quick_item(window, "workspaceSubtitleEditor")
        add_button = self._quick_visual_item(subtitle_editor, "workspaceSubtitleAddButton")
        self._click(window, add_button)

        self.assertEqual(self.app.segmentCount, 1)
        self.assertEqual(self.app.segmentAt(0)["start"], 2.5)
        subtitle_settings = self._quick_item(window, "workspaceSubtitleSettings")
        text_area = self._quick_visual_item(subtitle_settings, "workspaceSubtitleTextArea")
        self.assertTrue(text_area.isVisible())

    def test_workspace_subtitle_text_edit_stays_with_original_selection(self) -> None:
        self._load_project(
            segments=[
                {
                    "id": "segment-a",
                    "start": 0,
                    "end": 2,
                    "text": "first",
                    "speaker": "Speaker_Alice",
                },
                {
                    "id": "segment-b",
                    "start": 3,
                    "end": 5,
                    "text": "second",
                    "speaker": "Speaker_Bob",
                },
            ]
        )
        _, window = self._load_qml()
        subtitle_settings = self._quick_item(window, "workspaceSubtitleSettings")
        text_area = self._quick_visual_item(subtitle_settings, "workspaceSubtitleTextArea")

        text_area.forceActiveFocus()
        text_area.setProperty("text", "edited first")
        self.app.processEvents()
        self.app.selectSegment(1)
        self.app.processEvents()
        self.app.selectEditMode("audio")
        self.app.processEvents()

        self.assertEqual(self.app.currentEditMode, "audio")
        self.assertEqual(self.app.selectedSegmentIndex, 1)
        self.assertEqual(self.app.segmentAt(0)["text"], "edited first")
        self.assertEqual(self.app.segmentAt(1)["text"], "second")

    def test_qml_processing_progress_reserves_space_above_application_log(self) -> None:
        self._load_project()
        self.app._processing_progress.start("render")
        self.app.progressDetailsChanged.emit()
        _, window = self._load_qml()
        self.gui.resize(window, 1220, 760)
        progress_panel = self._quick_item(window, "processingProgressOverlay")
        log_panel = self._quick_item(window, "applicationLogPanel")
        central_column = self._quick_item(window, "contextActionBar").parentItem()
        layout_items = [
            self._quick_item(window, name)
            for name in (
                "contextActionBar",
                "mainVideoPanel",
                "processingProgressOverlay",
                "applicationLogPanel",
            )
        ]

        self.assertTrue(progress_panel.isVisible())
        self.assertGreater(progress_panel.height(), 0)
        for item in layout_items:
            self.assertGreater(item.width(), 0)
            self.assertGreater(item.height(), 0)
            self.assertGreaterEqual(item.y(), -1)
            self.assertLessEqual(item.y() + item.height(), central_column.height() + 1)
        self.assertLessEqual(progress_panel.y() + progress_panel.height(), log_panel.y() + 1)
        self.assertGreaterEqual(log_panel.y(), progress_panel.y() + progress_panel.height())

        self._click(window, self._quick_item(window, "applicationLogToggleButton"))
        self.gui.wait_until(
            lambda: bool(log_panel.property("expanded"))
            and all(
                item.y() + item.height() <= central_column.height() + 1
                for item in layout_items
            ),
            description="expanded application log below processing progress",
        )
        self.assertTrue(log_panel.property("expanded"))
        self.assertGreater(log_panel.height(), 0)
        for item in layout_items:
            self.assertLessEqual(item.y() + item.height(), central_column.height() + 1)
        self.assertLessEqual(log_panel.y() + log_panel.height(), central_column.height() + 1)

    def test_short_mode_keeps_progress_controls_visible_during_export(self) -> None:
        self._load_project()
        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "shortModeOpenButton"))

        self.app._processing_progress.start("render_short")
        self.app._running = True
        self.app.progressDetailsChanged.emit()
        self.app.runningChanged.emit()
        self.app.activeJobChanged.emit()
        self.app.processEvents()

        short_page = self._quick_item(window, "shortModePage")
        mode_overlay = self._quick_item(window, "processingProgressModeOverlay")
        mode_panel = self._quick_item(window, "processingProgressModePanel")
        self.assertTrue(short_page.isVisible())
        self.assertTrue(mode_overlay.isVisible())
        self.assertTrue(mode_panel.isVisible())
        stop_button = self._quick_visual_item(mode_panel, "processingProgressStopButton")
        self.assertTrue(stop_button.isVisible())

    def test_qml_zero_advanced_settings_are_preserved_in_round_trip(self) -> None:
        self.app._settings.update(
            {
                "subtitle_max_gap_seconds": 0.0,
                "subtitle_end_padding_seconds": 0.0,
                "subtitle_min_duration_seconds": 0.0,
                "no_speech_min_seconds": 0.0,
                "speech_padding_seconds": 0.0,
                "audio_target_lufs": 0.0,
                "subtitle_volume_scale_percent": 0,
            }
        )

        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "saveSettingsButton"))
        self.app.processEvents()

        self.assertEqual(self.app.settings["subtitle_max_gap_seconds"], 0.0)
        self.assertEqual(self.app.settings["subtitle_end_padding_seconds"], 0.0)
        self.assertEqual(self.app.settings["subtitle_min_duration_seconds"], 0.0)
        self.assertEqual(self.app.settings["no_speech_min_seconds"], 0.0)
        self.assertEqual(self.app.settings["speech_padding_seconds"], 0.0)
        self.assertEqual(self.app.settings["audio_target_lufs"], 0.0)
        self.assertEqual(self.app.settings["subtitle_volume_scale_percent"], 0)

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
        message_start = len(self.gui.messages)
        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "editSubtitlesButton"))
        timeline = self._quick_item(window, "editorTimeline")
        self.gui.set_property(
            timeline,
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
        self.gui.set_property(timeline, "visibleSegments", [])

        self.gui.assert_no_messages_containing("TypeError", since=message_start)

    def test_qml_mixer_close_does_not_run_callbacks_in_destroyed_context(self) -> None:
        self._load_project()
        message_start = len(self.gui.messages)
        _, window = self._load_qml()
        for _ in range(3):
            self._click(window, self._quick_item(window, "audioMixerOpenButton"))
            self.assertIsNotNone(window.findChild(QObject, "mixerPreviewPlayers"))
            self.app.updateAudioMixChannel(0, {"muted": True})
            self.app.updateAudioMixChannel(0, {"muted": False})
            self.app.processEvents()
            self._click(window, self._quick_item(window, "mixerBackButton"))
            self.gui.wait_until(
                lambda: window.findChild(QObject, "mixerContent") is None,
                description="mixer loader cleanup",
            )

        self.gui.assert_no_messages_containing(
            "invalid context",
            "syncPreviewPlayer",
            since=message_start,
        )

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

    def test_codex_chat_error_does_not_replace_workflow_status(self) -> None:
        self.app._status = "ショート動画を書き出しています"
        self.app._stage = "ENCODE"

        self.app._on_codex_chat_state(
            CodexChatSnapshot(
                connection_state="error",
                auth_state="error",
                chat_state="disconnected",
                error="Codexへ接続できません",
            )
        )

        self.assertEqual(self.app.status, "ショート動画を書き出しています")
        self.assertEqual(self.app.stage, "ENCODE")

    def test_codex_model_persistence_does_not_replace_workflow_status(self) -> None:
        self.app._settings["codex_model"] = ""
        self.app._status = "文字起こしを実行しています"
        self.app._stage = "TRANSCRIBE"

        self.app._persist_codex_model("gpt-default")

        self.assertEqual(self.app.status, "文字起こしを実行しています")
        self.assertEqual(self.app.stage, "TRANSCRIBE")
        payload = json.loads(self.app.gui_config_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["shared"]["codex_model"], "gpt-default")

    def test_codex_chat_connects_during_backend_startup(self) -> None:
        self.assertEqual(self._codex_chat_connect_calls, 1)

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
        self.assertIn("プレビュー", cache_summary.property("text"))
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

    def _generate_test_video(self, path: Path) -> None:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=1:size=320x240:rate=1",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                str(path),
            ],
            check=True,
            capture_output=True,
        )

    def _generate_short_mode_test_video(self, path: Path) -> None:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=15:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
            ],
            check=True,
            capture_output=True,
        )

    def _generate_black_test_video_with_audio(self, video: Path, audio: Path) -> None:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:size=320x180:rate=15:duration=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=1",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(video),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:sample_rate=48000:duration=1",
                "-c:a",
                "flac",
                str(audio),
            ],
            check=True,
            capture_output=True,
        )

    def _generate_silence_cut_test_media(self, video: Path, audio: Path) -> None:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=15:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(video),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=3",
                "-af", "volume=0:enable=between(t\\,1\\,2)",
                "-c:a", "flac", str(audio),
            ],
            check=True,
            capture_output=True,
        )

    def _extract_gray_frame(self, video: Path, at_seconds: float = 0.5) -> bytes:
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(at_seconds),
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "format=gray",
                "-f",
                "rawvideo",
                "-",
            ],
            check=True,
            capture_output=True,
        )
        return result.stdout

    def _probe_video_output(self, video: Path) -> tuple[float, str]:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "format=duration:stream=pix_fmt", "-of", "json", str(video),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        return float(payload["format"]["duration"]), str(payload["streams"][0]["pix_fmt"])

    def _measure_audio_mean_volume(self, media: Path, audio_filter: str = "") -> float:
        filters = f"{audio_filter},volumedetect" if audio_filter else "volumedetect"
        result = subprocess.run(
            [
                "ffmpeg", "-v", "info", "-i", str(media), "-map", "0:a:0", "-vn",
                "-af", filters, "-f", "null", os.devnull,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        matches = re.findall(r"mean_volume:\s*(-?[0-9]+(?:\.[0-9]+)?) dB", result.stderr)
        self.assertTrue(matches, result.stderr)
        return float(matches[-1])

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe required",
    )
    def test_editor_render_e2e_saves_edits_and_burns_subtitles(self) -> None:
        project_path = self._load_project(
            segments=[
                {
                    "id": "render-e2e",
                    "start": 0.05,
                    "end": 0.95,
                    "text": "before edit",
                    "speaker": "Speaker_Alice",
                    "words": [],
                }
            ]
        )
        video = self.root / "game.mkv"
        audio = self.root / "1-alice.flac"
        self._generate_black_test_video_with_audio(video, audio)
        process_python = shutil.which("python.exe" if os.name == "nt" else "python3") or sys.executable
        self.app.workspace_root = Path(__file__).resolve().parents[1]
        captured_options: dict[str, object] = {}

        def build_test_command(config_path: Path, **kwargs: object) -> list[str]:
            captured_options["config_path"] = config_path
            captured_options.update(kwargs)
            return [
                process_python,
                "-u",
                "-m",
                "src.subtitle_workflow",
                "render",
                "--project",
                str(kwargs["project_path"]),
                "--config",
                str(config_path),
                "--run",
            ]

        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "editSubtitlesButton"))
        editor = self._quick_item(window, "editorPage")
        QTest.qWait(100)
        caption = self._quick_visual_item(
            self._quick_item(window, "captionTable"),
            "captionTextArea",
        )
        caption.forceActiveFocus()
        caption.setProperty("text", "E2E BURNED CAPTION")
        self.app.processEvents()

        progress_changes = QSignalSpy(self.app.progressChanged)
        finished = QSignalSpy(self.app.process.finished)
        with patch("src.gui.build_gui_render_command", side_effect=build_test_command):
            self._click(window, self._quick_item(window, "editorRenderButton"))
            if finished.count() == 0:
                self.assertTrue(finished.wait(30_000), self.app.process.errorString())
            self.app.processEvents()

        self.assertEqual(Path(captured_options["config_path"]).resolve(), self.app.gui_config_path.resolve())
        self.assertEqual(Path(captured_options["project_path"]).resolve(), project_path.resolve())
        self.assertFalse(editor.isVisible())
        self.assertTrue(self._quick_item(window, "mainWorkspace").isVisible())
        self.assertEqual(
            self.app.stage,
            "COMPLETE",
            f"{self.app.status}\n{self.app._log}",
        )
        self.assertEqual(self.app.progress, 1.0)
        self.assertGreaterEqual(progress_changes.count(), 3)
        self.assertIn("ASS preview ready", self.app._log)
        self.assertIn("Rendering edited", self.app._log)
        self.assertIn("Render complete", self.app._log)

        saved_project = load_project(project_path)
        self.assertEqual(saved_project["segments"][0]["text"], "E2E BURNED CAPTION")
        self.assertTrue(saved_project["segments"][0]["manual_text"])
        self.assertGreater(saved_project["subtitle_settings"]["font_size"], 0)
        output = Path(saved_project["render_settings"]["last_output"])
        self.assertTrue(output.is_file())
        self.assertGreater(output.stat().st_size, 0)

        source_frame = self._extract_gray_frame(video)
        output_frame = self._extract_gray_frame(output)
        self.assertEqual(len(output_frame), len(source_frame))
        self.assertGreater(max(output_frame), max(source_frame) + 80)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe required",
    )
    def test_silence_cut_gui_render_e2e_retimes_and_burns_subtitles(self) -> None:
        project_path = self._load_project(
            segments=[
                {
                    "id": "before-silence",
                    "start": 0.15,
                    "end": 0.85,
                    "text": "FIRST CAPTION",
                    "speaker": "Speaker_Alice",
                    "words": [],
                },
                {
                    "id": "after-silence",
                    "start": 2.15,
                    "end": 2.85,
                    "text": "RETIMED CAPTION",
                    "speaker": "Speaker_Alice",
                    "words": [],
                },
            ]
        )
        video = self.root / "game.mkv"
        audio = self.root / "1-alice.flac"
        self._generate_silence_cut_test_media(video, audio)
        self.app._project["video"]["duration_seconds"] = 3.0
        save_project(project_path, self.app._project)
        process_python = shutil.which("python.exe" if os.name == "nt" else "python3") or sys.executable
        self.app.workspace_root = Path(__file__).resolve().parents[1]

        def build_test_command(config_path: Path, **kwargs: object) -> list[str]:
            return [
                process_python, "-u", "-m", "src.subtitle_workflow", "render",
                "--project", str(kwargs["project_path"]),
                "--config", str(config_path), "--run",
            ]

        _, window = self._load_qml()
        self._quick_item(window, "silenceSwitch").setProperty("checked", True)
        self._quick_item(window, "silenceField").setProperty("text", "0.5")
        self._quick_item(window, "speechPaddingField").setProperty("text", "0.00")
        self._quick_item(window, "speechThresholdField").setProperty("text", "-35")
        self._quick_item(window, "normalizeSwitch").setProperty("checked", False)
        self.app.processEvents()

        finished = QSignalSpy(self.app.process.finished)
        with patch("src.gui.build_gui_render_command", side_effect=build_test_command):
            self._click(window, self._quick_item(window, "renderVideoButton"))
            if finished.count() == 0:
                self.assertTrue(finished.wait(60_000), self.app.process.errorString())
            self.app.processEvents()

        self.assertEqual(self.app.stage, "COMPLETE", f"{self.app.status}\n{self.app._log}")
        self.assertIn("Cutting 1 silent ranges", self.app._log)
        self.assertIn("Rendering edited subtitles", self.app._log)
        self.assertIn("Render complete", self.app._log)

        saved_project = load_project(project_path)
        render_settings = saved_project["render_settings"]
        self.assertTrue(render_settings["cut_no_speech"])
        self.assertEqual(render_settings["no_speech_min_seconds"], 0.5)
        self.assertEqual(render_settings["speech_padding_seconds"], 0.0)
        self.assertEqual(render_settings["speech_threshold_db"], "-35dB")
        cut_output = Path(render_settings["last_cut_output"])
        output = Path(render_settings["last_output"])
        self.assertTrue(cut_output.is_file())
        self.assertTrue(output.is_file())

        source_duration, _ = self._probe_video_output(video)
        cut_duration, cut_pixel_format = self._probe_video_output(cut_output)
        output_duration, output_pixel_format = self._probe_video_output(output)
        self.assertLess(cut_duration, source_duration - 0.5)
        self.assertAlmostEqual(output_duration, cut_duration, delta=0.2)
        self.assertEqual(cut_pixel_format, "yuv420p")
        self.assertEqual(output_pixel_format, "yuv420p")

        cut_frame = self._extract_gray_frame(cut_output, 1.5)
        output_frame = self._extract_gray_frame(output, 1.5)
        self.assertEqual(len(output_frame), len(cut_frame))
        self.assertGreater(max(output_frame), max(cut_frame) + 80)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe required",
    )
    def test_audio_mixer_gui_settings_are_saved_and_applied_to_rendered_output(self) -> None:
        project_path = self._load_project()
        video = self.root / "game.mkv"
        external_audio = self.root / "1-alice.flac"
        self._generate_black_test_video_with_audio(video, external_audio)
        process_python = shutil.which("python.exe" if os.name == "nt" else "python3") or sys.executable
        self.app.workspace_root = Path(__file__).resolve().parents[1]

        def build_test_command(config_path: Path, **kwargs: object) -> list[str]:
            return [
                process_python, "-u", "-m", "src.subtitle_workflow", "render",
                "--project", str(kwargs["project_path"]),
                "--config", str(config_path), "--run",
            ]

        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "audioMixerOpenButton"))
        QTest.qWait(100)
        self.assertTrue(self._quick_item(window, "mixerPage").isVisible())
        channel_list = self._quick_item(window, "mixerChannelList")

        channels = self.app.audioMixerChannels
        video_index = next(index for index, channel in enumerate(channels) if channel["kind"] == "video")
        external_index = next(index for index, channel in enumerate(channels) if channel["kind"] == "external")
        video_id = str(channels[video_index]["id"])
        external_id = str(channels[external_index]["id"])

        video_strip = self._quick_visual_item(channel_list, f"mixerChannelStrip-{video_index}")
        video_mute_button = self._quick_visual_item(video_strip, "mixerMuteButton")
        self.assertTrue(QMetaObject.invokeMethod(video_mute_button, "clicked"))
        QTest.qWait(50)
        external_strip = self._quick_visual_item(channel_list, f"mixerChannelStrip-{external_index}")
        if not bool(channels[external_index]["enabled"]):
            enabled_check = self._quick_visual_item(external_strip, "mixerChannelEnabledCheck")
            enabled_check.setProperty("checked", True)
            self.assertTrue(QMetaObject.invokeMethod(enabled_check, "toggled"))
            QTest.qWait(50)
            external_strip = self._quick_visual_item(channel_list, f"mixerChannelStrip-{external_index}")
        external_solo_button = self._quick_visual_item(external_strip, "mixerSoloButton")
        self.assertTrue(QMetaObject.invokeMethod(external_solo_button, "clicked"))
        QTest.qWait(50)
        external_strip = self._quick_visual_item(channel_list, f"mixerChannelStrip-{external_index}")
        fader = self._quick_visual_item(external_strip, "mixerChannelFader")
        fader.setProperty("value", -6.0)
        self.assertTrue(QMetaObject.invokeMethod(fader, "moved"))
        QTest.qWait(50)

        updated_channels = self.app.audioMixerChannels
        updated_video = next(channel for channel in updated_channels if str(channel["id"]) == video_id)
        updated_external = next(channel for channel in updated_channels if str(channel["id"]) == external_id)
        self.assertTrue(updated_video["muted"])
        self.assertTrue(updated_external["enabled"])
        self.assertTrue(updated_external["solo"])
        self.assertGreater(float(updated_external["volume_percent"]), 35.0)
        self.assertLess(float(updated_external["volume_percent"]), 65.0)
        configured_volume = float(updated_external["volume_percent"])

        self._quick_item(window, "normalizeSwitch").setProperty("checked", False)
        finished = QSignalSpy(self.app.process.finished)
        with patch("src.gui.build_gui_render_command", side_effect=build_test_command):
            self._click(window, self._quick_item(window, "mixerRenderButton"))
            if finished.count() == 0:
                self.assertTrue(finished.wait(60_000), self.app.process.errorString())
            self.app.processEvents()

        self.assertEqual(self.app.stage, "COMPLETE", f"{self.app.status}\n{self.app._log}")
        self.assertIn("Rendering edited subtitles", self.app._log)
        self.assertIn("Render complete", self.app._log)
        saved_project = load_project(project_path)
        self.assertTrue(saved_project["audio_mix"]["customized"])
        saved_video = next(
            channel for channel in saved_project["audio_mix"]["channels"] if str(channel["id"]) == video_id
        )
        saved_external = next(
            channel for channel in saved_project["audio_mix"]["channels"] if str(channel["id"]) == external_id
        )
        self.assertTrue(saved_video["muted"])
        self.assertTrue(saved_external["enabled"])
        self.assertTrue(saved_external["solo"])
        self.assertAlmostEqual(float(saved_external["volume_percent"]), configured_volume, delta=0.1)

        output = Path(saved_project["render_settings"]["last_output"])
        self.assertTrue(output.is_file())
        output_mean_volume = self._measure_audio_mean_volume(output)
        reference_mean_volume = self._measure_audio_mean_volume(
            external_audio,
            "aresample=48000:async=1:first_pts=0,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={configured_volume / 100.0:.4f}",
        )
        self.assertAlmostEqual(output_mean_volume, reference_mean_volume, delta=2.0)
        video_band_volume = self._measure_audio_mean_volume(output, "bandpass=f=440:w=80")
        external_band_volume = self._measure_audio_mean_volume(output, "bandpass=f=880:w=80")
        self.assertGreater(external_band_volume, video_band_volume + 15.0)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe required",
    )
    def test_video_source_selection_via_gui_updates_source_panel_and_backend(self) -> None:
        video = self.root / "test_video.mp4"
        self._generate_test_video(video)

        self._media_probe_patch.stop()
        _, window = self._load_qml()

        self._click(window, self._quick_item(window, "sourceSetupButton"))
        self.app.processEvents()

        video_label = self._quick_item(window, "sourceVideoPathText")
        self.assertEqual(video_label.property("text"), "未選択")

        self.app.setVideoFile(str(video))
        self.app.processEvents()

        self.assertEqual(self.app.sourceSelection["video"], str(video.resolve()))
        self.assertIn(video.name, video_label.property("text"))
        self.assertEqual(self.app.stage, "INPUT")
        self.assertIn("話者音声", self.app.status)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe required",
    )
    def test_ffprobe_failure_during_video_selection_is_diagnosable_in_gui(self) -> None:
        bad_video = self.root / "fake_video.mp4"
        bad_video.write_text("not a video file", encoding="utf-8")

        self._media_probe_patch.stop()
        _, window = self._load_qml()

        self._click(window, self._quick_item(window, "sourceSetupButton"))
        self.app.processEvents()

        video_label = self._quick_item(window, "sourceVideoPathText")
        self.assertEqual(video_label.property("text"), "未選択")

        self.app.setVideoFile(str(bad_video))
        self.app.processEvents()

        self.assertEqual(self.app.sourceSelection["video"], "")
        self.assertEqual(video_label.property("text"), "未選択")
        self.assertEqual(self.app.stage, "CHECK")
        self.assertIn("検証に失敗", self.app.status)

    def test_short_mode_screen_opens_and_closes(self) -> None:
        self._load_project()
        _, window = self._load_qml()

        open_button = self._quick_item(window, "shortModeOpenButton")
        self.assertTrue(open_button.property("visible"))
        self.assertTrue(open_button.property("enabled"))

        self._click(window, open_button)
        short_page = self._quick_item(window, "shortModePage")
        self.assertTrue(short_page.property("visible"))

        back_button = self._quick_item(window, "shortModeBackButton")
        self._click(window, back_button)
        self.assertFalse(short_page.property("visible"))

    def test_short_mode_transition_duration_uses_internal_values(self) -> None:
        self._load_project()
        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "shortModeOpenButton"))

        transition_combo = self._quick_item(window, "shortModeTransitionCombo")
        duration_slider = self._quick_item(window, "shortModeTransitionDurationSlider")
        for transition_type, duration in (("crossfade", 0.4), ("fade", 0.8), ("cut", 1.2)):
            self.assertTrue(self.app.setShortVideoTransition(transition_type, 0.0))
            self.app.processEvents()
            self.assertEqual(transition_combo.property("currentValue"), transition_type)

            duration_slider.setProperty("value", duration)
            self.app.processEvents()

            transition = self.app.shortVideoSettings["transition"]
            self.assertEqual(transition["type"], transition_type)
            self.assertAlmostEqual(float(transition["duration"]), duration)

    def test_short_mode_clip_list_and_preview(self) -> None:
        segments = [
            {
                "id": "seg-1",
                "start": 1.0,
                "end": 3.0,
                "text": "first clip",
                "speaker": "Speaker_Alice",
                "words": [],
            },
            {
                "id": "seg-2",
                "start": 5.0,
                "end": 8.0,
                "text": "second clip",
                "speaker": "Speaker_Bob",
                "words": [],
            },
        ]
        self._load_project(segments=segments)
        _, window = self._load_qml()

        open_button = self._quick_item(window, "shortModeOpenButton")
        self._click(window, open_button)
        QTest.qWait(100)

        short_page = self._quick_item(window, "shortModePage")
        self.assertTrue(short_page.property("visible"))

        preview = self._quick_item(window, "shortModePreview")
        clip_list = self._quick_item(window, "shortModeClipList")
        settings_panel = self._quick_item(window, "shortModeSettingsPanel")
        self.assertTrue(preview.property("visible"))
        self.assertTrue(clip_list.property("visible"))
        self.assertTrue(settings_panel.property("visible"))

        clip_view = self._quick_item(window, "shortModeClipListView")
        self.assertEqual(clip_view.property("count"), 2)

        self.assertIsNotNone(preview.property("clipData"))
        self.assertEqual(preview.property("clipData").get("segment_id"), "seg-1")

        self.assertEqual(len(self.app.shortVideoClips), 2)
        self.assertEqual(self.app.shortVideoClips[1]["segment_id"], "seg-2")
        self.assertEqual(self.app.shortVideoSettings["global_fit"], "cover")

        # remove second clip and reorder the remaining clip
        self.app.removeShortVideoClip(1)
        self.assertEqual(len(self.app.shortVideoClips), 1)
        self.app.moveShortVideoClip(0, 0)
        self.assertEqual(len(self.app.shortVideoClips), 1)

        back_button = self._quick_item(window, "shortModeBackButton")
        self._click(window, back_button)
        self.assertFalse(short_page.property("visible"))

    def test_short_mode_clip_trimming_is_limited_to_source_segment(self) -> None:
        self._load_project(
            segments=[
                {
                    "id": "trim-segment",
                    "start": 1.0,
                    "end": 3.0,
                    "text": "trim me",
                    "speaker": "Speaker_Alice",
                    "words": [],
                }
            ]
        )
        self.app.initializeShortVideoClips()

        self.assertTrue(self.app.updateShortVideoClip(0, {"start": 1.5, "end": 2.5}))
        self.assertFalse(self.app.updateShortVideoClip(0, {"start": 0.5}))
        self.assertFalse(self.app.updateShortVideoClip(0, {"end": 3.5}))
        self.assertFalse(self.app.updateShortVideoClip(0, {"start": 2.5, "end": 2.5}))
        self.assertEqual(self.app.shortVideoClips[0]["start"], 1.5)
        self.assertEqual(self.app.shortVideoClips[0]["end"], 2.5)

    def test_short_mode_visual_clip_updates_do_not_require_trim_metadata(self) -> None:
        self._load_project(
            segments=[
                {
                    "id": "trim-segment",
                    "start": 1.0,
                    "end": 3.0,
                    "text": "trim me",
                    "speaker": "Speaker_Alice",
                    "words": [],
                }
            ]
        )
        self.app.initializeShortVideoClips()
        self.app._project["short_video"]["clips"][0]["segment_id"] = "missing-segment"

        self.assertTrue(self.app.updateShortVideoClip(0, {"fit": "blur"}))
        self.assertEqual(self.app.shortVideoClips[0]["fit"], "blur")
        self.assertFalse(self.app.updateShortVideoClip(0, {"start": float("nan")}))
        self.assertFalse(self.app.updateShortVideoClip(0, {"end": float("inf")}))

    def test_empty_short_mode_adds_and_trims_direct_range_clip(self) -> None:
        _video, _audio, _output = self._set_ready_sources()
        with patch("src.gui.probe_media_duration", return_value=3.0):
            self.assertTrue(self.app.createEmptyProject())

        self.assertEqual(self.app.subtitleSegments, [])
        self.assertTrue(self.app.addShortVideoClipByRange(0.25, 1.5))
        self.assertEqual(self.app.shortVideoClips[0]["segment_id"], "")
        self.assertTrue(self.app.updateShortVideoClip(0, {"start": 0.5, "end": 1.25}))
        self.assertEqual(
            (self.app.shortVideoClips[0]["start"], self.app.shortVideoClips[0]["end"]),
            (0.5, 1.25),
        )
        self.assertFalse(self.app.addShortVideoClipByRange(2.0, 4.0))

    def test_short_mode_gui_adds_direct_range_clip_when_segments_exist(self) -> None:
        self._load_project(
            segments=[
                {
                    "id": "subtitle-segment",
                    "start": 1.0,
                    "end": 3.0,
                    "text": "字幕の範囲",
                    "speaker": "Speaker_Alice",
                    "words": [],
                }
            ]
        )

        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "shortModeOpenButton"))
        source_combo = self._quick_item(window, "shortModeClipSourceCombo")
        source_combo.setProperty("currentIndex", 1)
        start_field = self._quick_item(window, "shortModeRangeStartField")
        end_field = self._quick_item(window, "shortModeRangeEndField")
        start_field.setProperty("text", "0.250")
        end_field.setProperty("text", "0.750")
        self.app.processEvents()

        add_button = self._quick_item(window, "shortModeAddClipButton")
        self.assertTrue(add_button.property("enabled"))
        self._click(window, add_button)

        clip = self.app.shortVideoClips[-1]
        self.assertEqual(clip["segment_id"], "")
        self.assertEqual((clip["start"], clip["end"]), (0.25, 0.75))

    def test_video_only_project_explains_disabled_transcription(self) -> None:
        self._load_project(segments=[])
        self._set_ready_sources()
        self.app._project["speakers"] = []
        self.app._project["audio_sources"] = []
        self.app._speakers = []
        self.app._audio_tracks = []
        self.app.speakersChanged.emit()
        self.app.audioTracksChanged.emit()

        _, window = self._load_qml()
        transcribe_button = self._quick_item(window, "transcribeButton")
        reason = self._quick_item(window, "workflowBlockReason")
        self.assertFalse(transcribe_button.isEnabled())
        self.assertIn("話者音声または動画内音声が必要", reason.property("text"))

    def test_empty_short_mode_gui_adds_range_clip_and_enables_export(self) -> None:
        _video, _audio, _output = self._set_ready_sources()
        with patch("src.gui.probe_media_duration", return_value=3.0):
            self.assertTrue(self.app.createEmptyProject())

        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "shortModeOpenButton"))
        start_field = self._quick_item(window, "shortModeRangeStartField")
        end_field = self._quick_item(window, "shortModeRangeEndField")
        start_field.setProperty("text", "0.250")
        end_field.setProperty("text", "1.500")
        self.app.processEvents()
        add_button = self._quick_item(window, "shortModeAddClipButton")
        self.assertTrue(add_button.property("enabled"))
        self._click(window, add_button)
        self.assertEqual(len(self.app.shortVideoClips), 1)

        with (
            patch.object(self.app, "refreshDependencies"),
            patch.object(self.app, "saveProject", return_value=True),
            patch.object(self.app, "_start_command") as start_command,
        ):
            self._click(window, self._quick_item(window, "shortModeExportButton"))
        self.assertEqual(start_command.call_args.args[1], "render_short")

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe required",
    )
    def test_short_mode_gui_export_e2e_renders_vertical_video_audio_and_subtitles(self) -> None:
        project_path = self._load_project(
            segments=[
                {
                    "id": "short-first",
                    "start": 0.2,
                    "end": 1.0,
                    "text": "FIRST SHORT",
                    "speaker": "Speaker_Alice",
                    "words": [],
                },
                {
                    "id": "short-second",
                    "start": 1.5,
                    "end": 2.3,
                    "text": "SECOND SHORT",
                    "speaker": "Speaker_Bob",
                    "words": [],
                },
            ]
        )
        self.assertIsNotNone(self.app._project)
        project = self.app._project
        assert project is not None
        video = Path(str(project["video"]["path"]))
        self._generate_short_mode_test_video(video)
        project["video"]["duration_seconds"] = 3.0
        project["short_video"] = {
            "enabled": True,
            "output": {"width": 180, "height": 320, "fps": 15},
            "global_fit": "contain",
            "global_background_color": "000000",
            "subtitle_scale_percent": 100,
            "transition": {"type": "cut", "duration": 0.0},
            "bgm": {"path": "", "in": 0.0, "out": 0.0, "start": 0.0, "volume": 0.3},
            "clips": [
                {"segment_id": "short-first", "start": 0.2, "end": 1.0, "fit": "contain"},
                {"segment_id": "short-second", "start": 1.5, "end": 2.3, "fit": "contain"},
            ],
        }
        self.app._mark_project_dirty()
        self.app.shortVideoChanged.emit()

        _, window = self._load_qml()
        self._click(window, self._quick_item(window, "shortModeOpenButton"))
        self.app.processEvents()
        export_button = self._quick_item(window, "shortModeExportButton")
        self.assertTrue(export_button.property("enabled"))
        self.app.workspace_root = Path(__file__).resolve().parents[1]
        self._click(window, export_button)

        for _ in range(600):
            self.app.processEvents()
            QTest.qWait(50)
            if not self.app.running and self.app.stage in {"COMPLETE", "ERROR"}:
                break
        self.assertEqual(self.app.stage, "COMPLETE", self.app._log)

        saved_project = load_project(project_path)
        output = Path(saved_project["render_settings"]["short_last_output"])
        self.assertTrue(output.is_file())
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height,pix_fmt",
                "-of", "json", str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        media = json.loads(probe.stdout)
        video_stream = next(item for item in media["streams"] if item["codec_type"] == "video")
        self.assertEqual((video_stream["width"], video_stream["height"]), (180, 320))
        self.assertEqual(video_stream["pix_fmt"], "yuv420p")
        self.assertTrue(any(item["codec_type"] == "audio" for item in media["streams"]))
        self.assertAlmostEqual(float(media["format"]["duration"]), 1.6, delta=0.25)

        first_subtitle_frame = self._extract_gray_frame(output, 0.4)
        second_subtitle_frame = self._extract_gray_frame(output, 1.2)
        self.assertTrue(first_subtitle_frame)
        self.assertTrue(second_subtitle_frame)
        self.assertGreater(max(first_subtitle_frame), 100)
        self.assertGreater(max(second_subtitle_frame), 100)


    def test_transcribe_without_existing_project_starts_transcription(self) -> None:
        self._set_ready_sources()

        _, window = self._load_qml()
        with patch.object(self.app, "_start_command") as start_command:
            self._click(window, self._quick_item(window, "transcribeButton"))
            self.app.processEvents()

            dialog = window.findChild(QObject, "overwriteProjectDialog")
            if dialog is not None:
                self.assertFalse(dialog.property("visible"))

            start_command.assert_called_once()
            command = start_command.call_args[0][0]
            self.assertEqual(start_command.call_args[0][1], "transcribe")
            self.assertNotIn("--overwrite-project", command)

    def test_transcription_gui_process_creates_and_loads_project(self) -> None:
        video, audio, output = self._set_ready_sources()
        project_path = output / "game.subtitle-project.json"
        template_path = self.root / "transcription-result-template.json"
        project = create_project(
            video_path=video,
            output_dir=output,
            audio_sources=[{"path": str(audio.resolve())}],
            speakers=[
                {
                    "name": "alice",
                    "style": "Speaker_alice",
                    "file_name": audio.name,
                    "track_key": "craig:alice",
                    "color": "#7FD957",
                    "path": str(audio.resolve()),
                }
            ],
            segments=[
                {
                    "id": "segment-e2e",
                    "start": 0.0,
                    "end": 1.0,
                    "text": "E2E transcription result",
                    "speaker": "Speaker_alice",
                    "words": [],
                }
            ],
            duration_seconds=1.0,
        )
        save_project(template_path, project)
        helper_path = Path(__file__).with_name("fake_transcription_process.py").resolve()
        process_python = shutil.which("python.exe" if os.name == "nt" else "python3") or sys.executable
        captured_options: dict[str, object] = {}

        def build_test_command(config_path: Path, **kwargs: object) -> list[str]:
            captured_options["config_path"] = config_path
            captured_options.update(kwargs)
            return [
                process_python,
                "-u",
                str(helper_path),
                "--template",
                str(template_path),
                "--project-path",
                str(project_path),
            ]

        _, window = self._load_qml()
        progress_changes = QSignalSpy(self.app.progressChanged)
        finished = QSignalSpy(self.app.process.finished)
        with patch("src.gui.build_gui_transcribe_command", side_effect=build_test_command):
            self._click(window, self._quick_item(window, "transcribeButton"))
            if finished.count() == 0:
                self.assertTrue(finished.wait(10_000), self.app.process.errorString())
            self.app.processEvents()

        self.assertEqual(Path(captured_options["config_path"]).resolve(), self.app.gui_config_path.resolve())
        self.assertEqual(captured_options["video"], str(video.resolve()))
        self.assertEqual(captured_options["audio_files"], [str(audio.resolve())])
        self.assertEqual(captured_options["output_dir"], str(output.resolve()))
        self.assertFalse(captured_options["overwrite_project"])
        self.assertTrue(self.app.gui_config_path.is_file())
        self.assertTrue(project_path.is_file())
        self.assertTrue(self.app.projectLoaded)
        self.assertEqual(Path(self.app.projectPath), project_path.resolve())
        self.assertEqual(self.app.stage, "EDIT")
        self.assertEqual(self.app.progress, 1.0)
        self.assertGreaterEqual(progress_changes.count(), 3)
        self.assertIn("Starting WhisperX", self.app._log)
        self.assertIn("Project ready", self.app._log)
        self.assertEqual(self.app.subtitleSegments[0]["text"], "E2E transcription result")

        edit_button = self._quick_item(window, "editSubtitlesButton")
        self.assertTrue(edit_button.isEnabled())
        self._click(window, edit_button)
        self.assertTrue(self._quick_item(window, "editorPage").isVisible())

    def test_followup_transcription_preserves_project_settings_for_merge_and_replace(self) -> None:
        project_path = self._load_project()
        project = self.app._project
        assert project is not None
        project["audio_mix"] = {
            "version": 1,
            "customized": True,
            "channels": [
                {
                    "id": "video:0:a:0",
                    "kind": "video",
                    "label": "game",
                    "selector": "0:a:0",
                    "enabled": True,
                    "muted": False,
                    "solo": False,
                    "volume_percent": 42.0,
                }
            ],
        }
        project["short_video"] = {
            "schema_version": 2,
            "enabled": True,
            "output": {"width": 720, "height": 1280, "fps": 30},
            "global_fit": "contain",
            "global_background_color": "112233",
            "subtitle_scale_percent": 80.0,
            "transition": {"type": "cut", "duration": 0.0},
            "bgm": {"path": "custom-bgm.wav", "in": 0.0, "out": 0.0, "start": 0.0, "volume": 0.4},
            "clips": [{"segment_id": "segment-a", "start": 1.0, "end": 2.0, "fit": "blur"}],
        }
        self.app._mark_project_dirty()
        self.assertTrue(self.app.saveProject())
        preserved = deepcopy(load_project(project_path))
        custom_project_path = project_path.with_name("custom-edit.subtitle-project.json")
        save_project(custom_project_path, preserved)
        generated = create_project(
            video_path=project_path.parent / "game.mkv",
            output_dir=project_path.parent / "export",
            segments=[
                {
                    "id": "transcribed-new",
                    "start": 5.0,
                    "end": 6.0,
                    "text": "new transcript",
                    "speaker": "Speaker_Alice",
                    "words": [],
                }
            ],
            duration_seconds=30.0,
            transcription={"engine": "new-engine"},
        )

        try:
            for mode in ("merge", "replace"):
                with self.subTest(mode=mode):
                    save_project(project_path, preserved)
                    self.app._project = deepcopy(generated)
                    self.app._project_path = str(project_path)
                    self.app._transcription_merge_mode = mode
                    self.app._transcription_preserved_project = deepcopy(preserved)
                    self.app._transcription_preserved_project_path = str(custom_project_path)

                    self.assertTrue(self.app._merge_preserved_transcription_segments())
                    saved = load_project(custom_project_path)
                    self.assertTrue(Path(self.app.projectPath).samefile(custom_project_path))
                    self.assertEqual(saved["audio_mix"], preserved["audio_mix"])
                    self.assertEqual(saved["short_video"], preserved["short_video"])
                    self.assertEqual(saved["transcription"], {"engine": "new-engine"})
                    expected_ids = {"segment-a", "transcribed-new"} if mode == "merge" else {"transcribed-new"}
                    self.assertEqual({item["id"] for item in saved["segments"]}, expected_ids)
                    self.assertEqual(load_project(project_path)["segments"], preserved["segments"])
        finally:
            self.app._transcription_merge_mode = ""
            self.app._transcription_preserved_segments = []
            self.app._transcription_preserved_project = None
            self.app._transcription_preserved_project_path = ""

    def test_followup_transcription_uses_private_project_path_without_overwriting_default(self) -> None:
        video, audio, output = self._set_ready_sources()
        default_path = output / "game.subtitle-project.json"
        custom_path = output / "custom-edit.subtitle-project.json"
        preserved = create_project(
            video_path=video,
            output_dir=output,
            audio_sources=[{"path": str(audio.resolve()), "file_name": audio.name}],
            speakers=[{"name": "alice", "style": "Speaker_alice", "path": str(audio.resolve())}],
            segments=[{"id": "keep", "start": 0.0, "end": 1.0, "text": "keep", "speaker": "alice"}],
            duration_seconds=2.0,
        )
        sentinel = deepcopy(preserved)
        sentinel["segments"][0]["text"] = "default sentinel"
        save_project(custom_path, preserved)
        save_project(default_path, sentinel)
        self.app._project = deepcopy(preserved)
        self.app._project_path = str(custom_path)
        self.app._source_selection = SourceSelection(
            video=str(video.resolve()),
            output_dir=str(output.resolve()),
            audio_files=(str(audio.resolve()),),
        )

        with patch.object(self.app, "startTranscription") as start_transcription:
            self.app.transcribeProject(self.app.settings, "merge")

        generated_path = Path(start_transcription.call_args.args[2])
        self.assertNotEqual(generated_path.resolve(), default_path.resolve())
        self.assertEqual(generated_path.parent.resolve(), output.resolve())
        self.assertTrue(generated_path.name.startswith(".game.subtitle-project."))
        self.assertEqual(load_project(default_path)["segments"], sentinel["segments"])

    def test_transcription_merge_failure_restores_project_and_keeps_error_status(self) -> None:
        project_path = self._load_project()
        preserved = deepcopy(self.app._project)
        assert preserved is not None
        generated = deepcopy(preserved)
        generated["segments"] = []
        self.app._project = generated
        self.app._project_path = str(project_path)
        self.app._active_job = "transcribe"
        self.app._running = True
        self.app._transcription_merge_mode = "merge"
        self.app._transcription_preserved_project = preserved
        self.app._transcription_preserved_project_path = str(project_path)

        with (
            patch.object(self.app, "_read_process_output"),
            patch.object(self.app, "_try_load_default_project", return_value=True),
            patch.object(
                self.app,
                "_merge_preserved_transcription_segments",
                side_effect=ValueError("merge failed"),
            ),
        ):
            self.app._process_finished(0, None)

        self.assertEqual(self.app.stage, "ERROR")
        self.assertIn("統合に失敗しました", self.app.status)
        self.assertEqual(load_project(project_path)["segments"], preserved["segments"])

    def test_processing_cancel_e2e_stops_process_and_restores_gui(self) -> None:
        self._set_ready_sources()
        helper_path = Path(__file__).with_name("fake_processing_process.py").resolve()
        process_python = shutil.which("python.exe" if os.name == "nt" else "python3") or sys.executable

        def build_wait_command(_config_path: Path, **_kwargs: object) -> list[str]:
            return [
                process_python,
                "-u",
                str(helper_path),
                "--mode",
                "wait",
            ]

        _, window = self._load_qml()
        started = QSignalSpy(self.app.process.started)
        finished = QSignalSpy(self.app.process.finished)
        with patch("src.gui.build_gui_transcribe_command", side_effect=build_wait_command):
            self._click(window, self._quick_item(window, "transcribeButton"))
            if started.count() == 0:
                self.assertTrue(started.wait(10_000), self.app.process.errorString())
            QTest.qWait(100)
            self.app.processEvents()

            self.assertTrue(self.app.running)
            self.assertEqual(self.app.stage, "WHISPERX")
            stop_button = self._quick_item(window, "saveSettingsButton")
            self.assertEqual(stop_button.property("text"), "停止")
            self._click(window, stop_button)
            if finished.count() == 0:
                self.assertTrue(finished.wait(10_000), self.app.process.errorString())
            self.app.processEvents()

        self.assertFalse(self.app.running)
        self.assertEqual(self.app.process.state(), QProcess.ProcessState.NotRunning)
        self.assertEqual(self.app.stage, "CANCELLED")
        self.assertIn("停止", self.app.status)
        self.assertEqual(self.app.activeJob, "")
        self.assertEqual(self._quick_item(window, "saveSettingsButton").property("text"), "設定を保存")
        self.assertTrue(self._quick_item(window, "transcribeButton").isEnabled())

    def test_cancelled_followup_transcription_cannot_merge_into_next_project(self) -> None:
        project_a_path = self._load_project()
        project_a = deepcopy(self.app._project)
        assert project_a is not None
        generated_a_path = self.root / ".project-a.transcribing.subtitle-project.json"
        save_project(generated_a_path, project_a)
        self.app._transcription_merge_mode = "merge"
        self.app._transcription_preserved_project = deepcopy(project_a)
        self.app._transcription_preserved_project_path = str(project_a_path)
        self.app._transcription_generated_project_path = str(generated_a_path)
        self.app._active_job = "transcribe"
        self.app._running = True
        self.app._cancel_requested = True

        with patch.object(self.app, "_read_process_output"):
            self.app._process_finished(1, QProcess.ExitStatus.NormalExit)

        self.assertEqual(self.app._transcription_merge_mode, "")
        self.assertIsNone(self.app._transcription_preserved_project)
        self.assertEqual(self.app._transcription_preserved_project_path, "")
        self.assertFalse(generated_a_path.exists())

        project_b_root = self.root / "project-b"
        project_b_root.mkdir()
        project_b_video = project_b_root / "project-b.mkv"
        project_b_video.write_bytes(b"video")
        project_b_path = project_b_root / "project-b.subtitle-project.json"
        project_b = create_project(
            video_path=project_b_video,
            output_dir=project_b_root,
            segments=[
                {
                    "id": "project-b-segment",
                    "start": 0.0,
                    "end": 1.0,
                    "text": "project B",
                    "speaker": "Speaker_B",
                }
            ],
            duration_seconds=1.0,
        )
        save_project(project_b_path, project_b)
        self.app._source_selection = SourceSelection(
            video=str(project_b_video.resolve()),
            output_dir=str(project_b_root.resolve()),
        )
        self.app._clear_project()
        self.app._active_job = "transcribe"
        self.app._running = True
        self.app._cancel_requested = False

        with patch.object(self.app, "_read_process_output"):
            self.app._process_finished(0, QProcess.ExitStatus.NormalExit)

        self.assertTrue(Path(self.app.projectPath).samefile(project_b_path))
        self.assertEqual(self.app.subtitleSegments[0]["text"], "project B")
        self.assertEqual(load_project(project_a_path)["segments"], project_a["segments"])

    def test_processing_failure_retry_e2e_recovers_and_loads_project(self) -> None:
        video, audio, output = self._set_ready_sources()
        project_path = output / "game.subtitle-project.json"
        template_path = self.root / "retry-result-template.json"
        project = create_project(
            video_path=video,
            output_dir=output,
            audio_sources=[{"path": str(audio.resolve())}],
            speakers=[
                {
                    "name": "alice",
                    "style": "Speaker_alice",
                    "file_name": audio.name,
                    "track_key": "craig:alice",
                    "color": "#7FD957",
                    "path": str(audio.resolve()),
                }
            ],
            segments=[
                {
                    "id": "retry-success",
                    "start": 0.0,
                    "end": 1.0,
                    "text": "retry completed",
                    "speaker": "Speaker_alice",
                    "words": [],
                }
            ],
            duration_seconds=1.0,
        )
        save_project(template_path, project)
        helper_path = Path(__file__).with_name("fake_processing_process.py").resolve()
        process_python = shutil.which("python.exe" if os.name == "nt" else "python3") or sys.executable
        attempts = 0

        def build_attempt_command(_config_path: Path, **_kwargs: object) -> list[str]:
            nonlocal attempts
            attempts += 1
            command = [
                process_python,
                "-u",
                str(helper_path),
                "--mode",
                "fail" if attempts == 1 else "success",
            ]
            if attempts > 1:
                command.extend(
                    [
                        "--template",
                        str(template_path),
                        "--project-path",
                        str(project_path),
                    ]
                )
            return command

        _, window = self._load_qml()
        with patch("src.gui.build_gui_transcribe_command", side_effect=build_attempt_command):
            first_finished = QSignalSpy(self.app.process.finished)
            self._click(window, self._quick_item(window, "transcribeButton"))
            if first_finished.count() == 0:
                self.assertTrue(first_finished.wait(10_000), self.app.process.errorString())
            self.app.processEvents()

            self.assertFalse(self.app.running)
            self.assertEqual(self.app.stage, "ERROR")
            self.assertIn("23", self.app.status)
            self.assertIn("input audio became unavailable", self.app.status)
            self.assertIn(
                "input audio became unavailable",
                self._quick_item(window, "workflowStatusText").property("text"),
            )
            self.assertTrue(self._quick_item(window, "transcribeButton").isEnabled())

            second_finished = QSignalSpy(self.app.process.finished)
            self._click(window, self._quick_item(window, "transcribeButton"))
            if second_finished.count() == 0:
                self.assertTrue(second_finished.wait(10_000), self.app.process.errorString())
            self.app.processEvents()

        self.assertEqual(attempts, 2)
        self.assertFalse(self.app.running)
        self.assertTrue(self.app.projectLoaded)
        self.assertEqual(self.app.stage, "EDIT")
        self.assertEqual(self.app.progress, 1.0)
        self.assertEqual(self.app.subtitleSegments[0]["text"], "retry completed")
        self.assertIn("input audio became unavailable", self.app._log)
        self.assertTrue(self._quick_item(window, "editSubtitlesButton").isEnabled())

    def test_transcribe_with_existing_project_shows_overwrite_confirmation(self) -> None:
        video, _audio, _output = self._set_ready_sources()
        project_path = _output / f"{video.stem}.subtitle-project.json"
        project_path.write_text("{}", encoding="utf-8")

        _, window = self._load_qml()
        with patch.object(self.app, "_start_command") as start_command:
            self._click(window, self._quick_item(window, "transcribeButton"))
            self.app.processEvents()

            dialog = window.findChild(QObject, "overwriteProjectDialog")
            self.assertIsNotNone(dialog)
            self.assertTrue(dialog.property("visible"))
            self.assertEqual(dialog.property("title"), "既存プロジェクトの上書き")
            start_command.assert_not_called()

    def test_transcribe_reject_overwrite_does_not_start(self) -> None:
        video, _audio, _output = self._set_ready_sources()
        project_path = _output / f"{video.stem}.subtitle-project.json"
        project_path.write_text("{}", encoding="utf-8")

        _, window = self._load_qml()
        with patch.object(self.app, "_start_command") as start_command:
            self._click(window, self._quick_item(window, "transcribeButton"))
            self.app.processEvents()

            dialog = window.findChild(QObject, "overwriteProjectDialog")
            self.assertIsNotNone(dialog)
            dialog.reject()
            self.app.processEvents()

            start_command.assert_not_called()
            self.assertFalse(dialog.property("visible"))

    def test_transcribe_accept_overwrite_passes_overwrite_flag(self) -> None:
        video, _audio, _output = self._set_ready_sources()
        project_path = _output / f"{video.stem}.subtitle-project.json"
        project_path.write_text("{}", encoding="utf-8")

        _, window = self._load_qml()
        with patch.object(self.app, "_start_command") as start_command:
            self._click(window, self._quick_item(window, "transcribeButton"))
            self.app.processEvents()

            dialog = window.findChild(QObject, "overwriteProjectDialog")
            self.assertIsNotNone(dialog)
            dialog.accept()
            self.app.processEvents()

            start_command.assert_called_once()
            command = start_command.call_args[0][0]
            self.assertEqual(start_command.call_args[0][1], "transcribe")
            self.assertIn("--overwrite-project", command)


    def _fake_update_info(self) -> updater.UpdateInfo:
        return updater.UpdateInfo(
            current_version="v0.1.0",
            latest_version="v0.2.0",
            release_notes="Release notes",
            download_url="https://example.com/app.zip",
            tag_name="v0.2.0",
            available=True,
        )

    def test_backend_check_for_updates_exposes_info(self) -> None:
        info = self._fake_update_info()
        with patch.object(updater, "fetch_latest_release", return_value=info) as fetch:
            self.app.checkForUpdates()
            QTest.qWait(50)
            while self.app._update_busy:
                self.app.processEvents()
                QTest.qWait(50)
            fetch.assert_called_once_with(self.app.workspace_root)

        self.assertTrue(self.app.updateAvailable)
        self.assertEqual(self.app.updateCurrentVersion, "v0.1.0")
        self.assertEqual(self.app.updateLatestVersion, "v0.2.0")
        self.assertEqual(self.app.updateReleaseNotes, "Release notes")
        self.assertEqual(self.app.updateDownloadUrl, "https://example.com/app.zip")
        self.assertFalse(self.app.updateBusy)

    def test_backend_apply_update_starts_update_command(self) -> None:
        self.app._update_info = self._fake_update_info()
        with patch.object(self.app.process, "start") as start:
            self.app.applyUpdate()
            start.assert_called_once()
            program, args = start.call_args[0]
            if sys.platform == "win32" and shutil.which("powershell.exe"):
                self.assertEqual(program, "powershell.exe")
                self.assertIn("-File", args)
                self.assertIn("update.ps1", " ".join(args))
                self.assertNotIn(self.app._update_info.download_url, args)
            else:
                self.assertEqual(program, sys.executable)
                self.assertIn("-m", args)
                self.assertIn("src.updater", args)
                self.assertIn(self.app._update_info.download_url, args)
            self.app.process.started.emit()
        self.assertEqual(self.app._active_job, "update")
        self.assertTrue(self.app.running)

        self.app.process.finished.emit(0, QProcess.ExitStatus.NormalExit)
        self.app.processEvents()
        self.assertFalse(self.app.running)
        self.assertEqual(self.app.stage, "UPDATE")
        self.assertIn("完了", self.app.status)

    def test_backend_apply_update_blocked_when_project_dirty(self) -> None:
        self.app._update_info = self._fake_update_info()
        self.app._project_dirty = True
        with patch.object(self.app.process, "start") as start:
            self.app.applyUpdate()
            start.assert_not_called()
        self.assertIn("未保存", self.app.status)

    def test_backend_update_cannot_be_cancelled_or_dismissed(self) -> None:
        self.app._update_info = self._fake_update_info()
        self.app._running = True
        self.app._active_job = "update"

        self.app.cancelProcessing()
        self.assertFalse(self.app._cancel_requested)
        self.assertIn("停止できません", self.app.status)

        self.app.dismissUpdateInfo()
        self.assertIsNotNone(self.app._update_info)
        self.assertIn("閉じられません", self.app.status)

    def test_backend_restart_application_launches_and_quits(self) -> None:
        with patch("src.gui.subprocess.Popen") as popen, patch.object(self.app, "quit") as quit:
            self.app.restartApplication()
            popen.assert_called_once()
            quit.assert_called_once()

    def test_qml_update_dialog_flow(self) -> None:
        _, window = self._load_qml()
        check_button = self._quick_item(window, "checkForUpdatesButton")
        dialog = window.findChild(QObject, "updateDialog")
        self.assertIsNotNone(dialog)
        self.assertFalse(dialog.property("visible"))

        with patch.object(updater, "fetch_latest_release", return_value=self._fake_update_info()):
            self._click(window, check_button)
            self.gui.wait_until(
                lambda: not self.app._update_busy,
                description="update check completion",
            )

        self.assertTrue(dialog.property("visible"))
        apply_button = self._quick_item(window, "applyUpdateButton")
        self.assertTrue(apply_button.property("visible"))

        self.app._update_download_active = True
        self.app._update_busy = True
        self.app._update_download_bytes = 128
        self.app._update_download_total = 256
        self.app._update_download_cancel = threading.Event()
        self.app.updateBusyChanged.emit()
        self.app.updateDownloadProgressChanged.emit()
        progress = self._quick_item(window, "updateDownloadProgressBar")
        cancel = self._quick_item(window, "cancelUpdateDownloadButton")
        self.gui.wait_until(
            lambda: progress.isVisible() and cancel.isVisible(),
            description="update download progress and cancel actions",
        )
        self.assertEqual(progress.property("value"), 128)
        self.assertEqual(progress.property("to"), 256)
        self._click(window, cancel)
        self.assertTrue(self.app._update_download_cancel.is_set())

        self.app.updateDownloadFinished.emit("", "ダウンロードをキャンセルしました")
        self.gui.wait_until(
            lambda: (
                not self.app.updateDownloadActive
                and not self.app.updateBusy
                and self.app.stage == "CANCELLED"
            ),
            description="cancelled update download completion",
        )
        self.app._update_package_ready = True
        self.app.updatePackageReadyChanged.emit()
        self.gui.wait_until(
            lambda: (
                apply_button.isVisible()
                and apply_button.isEnabled()
                and apply_button.property("text") == "再起動して更新"
            ),
            description="verified update package action",
        )
        self.app._update_package_ready = False
        self.app.updatePackageReadyChanged.emit()

        with patch.object(self.app.process, "start"):
            self._click(window, apply_button)

        self.app.process.finished.emit(0, QProcess.ExitStatus.NormalExit)
        self.app.processEvents()
        restart_button = self._quick_item(window, "restartApplicationButton")
        self.assertTrue(restart_button.property("visible"))


if __name__ == "__main__":
    unittest.main()
