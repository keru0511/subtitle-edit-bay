from __future__ import annotations

import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QProcess

from src.gui import EditBayBackend
from src.gui_state import SourceSelection
from src.runtime_dependencies import RuntimeDependencyStatus


class EditBayGuiTestSession:
    """Owns the shared backend and resets all mutable test state in one place."""

    def __init__(self) -> None:
        self._workspace = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self._workspace.name)
        with patch("src.gui.CodexChatController.connect") as connect:
            self.backend = EditBayBackend([], workspace_root=self.workspace_root)
            self.codex_chat_connect_calls = connect.call_count
        self.startup_log_text = self.backend.logText
        self._base_config = deepcopy(self.backend._config)
        self._base_settings = deepcopy(self.backend.settings)
        self._base_transcription_context = deepcopy(self.backend._transcription_context)
        self._base_codex_session_snapshot = self.backend._codex_session.snapshot
        self._base_codex_chat_snapshot = self.backend._codex_chat.snapshot
        self._closed = False

    def prepare_test(self, test_name: str) -> Path:
        if self._closed:
            raise RuntimeError("GUI test session is already closed")
        root = self.workspace_root / test_name
        root.mkdir(parents=True)
        self._stop_background_work()
        self._reset_backend(root)
        return root

    def _reset_backend(self, root: Path) -> None:
        app = self.backend
        app.workspace_root = root
        app.gui_config_path = root / ".gui" / "runtime_config.json"
        app.color_config_path = root / "assets" / "speaker_colors.json"

        app._project = None
        app._project_path = ""
        app._project_dirty = False
        app._undo_stack.clear()
        app._redo_stack.clear()
        app._selected_segment_index = -1
        app._project_revision = 0
        app._subtitle_model.set_segments([])
        app._segment_starts.clear()
        app._segment_prefix_max_end.clear()

        app._active_job = ""
        app._processing_progress.start("")
        app._ffmpeg_duration_seconds = 0.0
        app._ffmpeg_duration_from_event = False
        app._processing_machine_event_seen = False
        app._ass_path = ""
        app._loading_project_sources = False
        app._relinking_project_sources = False
        app._relink_source_selection = None
        app._source_selection = SourceSelection()
        app._speakers = []
        app._audio_tracks = app._default_audio_tracks()
        app._alignment_result = app._empty_alignment_result()
        app._alignment_busy = False
        app._dependencies = RuntimeDependencyStatus(
            ffmpeg=True,
            ffprobe=True,
            whisperx=True,
            cuda=True,
        )
        app._config = deepcopy(self._base_config)
        app._settings = deepcopy(self._base_settings)
        app._transcription_context = deepcopy(self._base_transcription_context)

        app._running = False
        app._status = "保存済み"
        app._stage = "READY"
        app._progress = 0.0
        app._log = ""
        app._application_logger.clear_memory()
        app._last_process_diagnostic = None
        app._pending_process_error = ""
        app._process_output_tail = ""
        app._elapsed_seconds = 0
        app._cancel_requested = False

        app._autosave_future = None
        app._autosave_revision = -1
        app._autosave_path = ""
        app._autosave_pending = False
        app._ignored_autosaves.clear()
        app._transcription_merge_mode = ""
        app._transcription_preserved_segments = []
        app._transcription_preserved_project = None
        app._transcription_preserved_project_path = ""
        app._transcription_generated_project_path = ""

        app._audio_preview_cache_request += 1
        app._audio_preview_cache_paths.clear()
        app._audio_preview_cache_future = None
        app._audio_preview_preparing = False
        app.audio_preview_cache_root = root / ".audio-preview-cache"
        app._audio_preview_gains.clear()
        app._audio_preview_levels.clear()
        app._audio_preview_pending_levels.clear()
        app._audio_master_level = 0.0
        app._audio_limiter_reduction_db = 0.0

        app._highlight_generation += 1
        app._highlight_cancel = threading.Event()
        app._highlight_candidates = []
        app._highlight_rejected = []
        app._highlight_status = "idle"
        app._highlight_progress = 0.0

        app._update_info = None
        app._update_error = ""
        app._update_busy = False
        app._update_package_path = None
        app._update_package_sha256 = ""
        app._update_package_ready = False
        app._update_download_bytes = 0
        app._update_download_total = 0
        app._update_download_speed = 0.0
        app._update_download_active = False
        app._update_download_cancel = threading.Event()

        app._codex_proposal = None
        app._codex_current_time = None
        app._last_codex_login_url = ""
        app._last_codex_log_state = None
        app._codex_session._snapshot = self._base_codex_session_snapshot
        app._codex_chat._snapshot = self._base_codex_chat_snapshot
        app._codex_chat._workspace_root = str(root.resolve())
        app._codex_chat._preferred_model = self._base_codex_chat_snapshot.selected_model
        app._codex_chat._message_sequence = 0
        app._codex_chat._active_assistant_id = ""
        app._codex_chat._thread_needs_resume = False
        app._codex_chat._stop_requested = False

        app.processEvents()

    def _stop_background_work(self) -> None:
        app = self.backend
        app.autosave_timer.stop()
        app.elapsed_timer.stop()
        app._audio_preview_level_timer.stop()
        app._audio_master_mixer.stop()
        app._highlight_cancel.set()
        app._highlight_generation += 1
        app._update_download_cancel.set()
        self._stop_codex_session()

        if app._autosave_future is not None:
            app._wait_for_autosave()
        future = app._audio_preview_cache_future
        if future is not None and not future.done():
            future.cancel()
        app._audio_preview_cache_request += 1
        app._audio_preview_cache_future = None
        app._audio_preview_preparing = False

        if app.process.state() != QProcess.ProcessState.NotRunning:
            app.process.kill()
            if not app.process.waitForFinished(1_000):
                raise AssertionError("GUI test leaked a running backend process")
        app.processEvents()

    def _stop_codex_session(self) -> None:
        session = self.backend._codex_session
        session.stop()
        thread = session._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
            if thread.is_alive():
                raise AssertionError("GUI test leaked a running Codex edit session")
        with session._state_lock:
            session._generation += 1
            session._client = None
            session._thread = None
            session._stop_event = threading.Event()

    def finish_test(self) -> None:
        self._stop_background_work()
        app = self.backend
        app._project_dirty = False
        app._running = False
        app._active_job = ""
        app._cancel_requested = False
        for output in app._audio_preview_outputs.values():
            output.deleteLater()
        app._audio_preview_outputs.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    def cleanup(self) -> None:
        if self._closed:
            return
        try:
            try:
                self.finish_test()
            finally:
                try:
                    self.backend._codex_chat.shutdown()
                finally:
                    self.backend._shutdown_executor()
        finally:
            self._workspace.cleanup()
            self._closed = True
