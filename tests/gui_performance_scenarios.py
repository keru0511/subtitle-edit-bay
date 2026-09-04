from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Property, QObject, QUrl, Slot
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtQuick import QQuickItem

from src.gui import EditBayBackend
from tests.gui_test_harness import (
    AllowedQmlMessage,
    EventLoopLatencyProbe,
    GuiTestHarness,
    MediaPlayerSignalProbe,
    summarize_durations_ms,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
QML_PATH = REPO_ROOT / "src" / "ui" / "Main.qml"
PRE_302_REFERENCE_REVISION = "b600e909ea7d891a827f9dc5031a6697eb2e7a64"


def _repository_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _comparison_qml_message_allowlist(revision: str) -> tuple[AllowedQmlMessage, ...]:
    if revision != PRE_302_REFERENCE_REVISION:
        return ()
    return (
        AllowedQmlMessage(
            pattern=(
                r'Parameter "(?:position|playbackState)" is not declared\. '
                r"Injection of parameters into signal handlers is deprecated\."
            ),
            reason="The pinned pre-#302 comparison source uses Qt's former implicit signal parameters.",
        ),
        AllowedQmlMessage(
            pattern=(
                r'Parameter "index" is not declared\. '
                r"Injection of parameters into signal handlers is deprecated\."
            ),
            reason="The pinned pre-#302 short-fit handler uses Qt's former implicit signal parameter.",
        ),
    )


def _peak_resident_set_bytes() -> int:
    if os.name != "nt":
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return peak if sys.platform == "darwin" else peak * 1_024

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    succeeded = ctypes.windll.psapi.GetProcessMemoryInfo(
        handle,
        ctypes.byref(counters),
        counters.cb,
    )
    return int(counters.PeakWorkingSetSize) if succeeded else 0


def _variant(value: object) -> object:
    to_variant = getattr(value, "toVariant", None)
    return to_variant() if callable(to_variant) else value


def _state_name(value: object) -> str:
    name = getattr(value, "name", None)
    return str(name if name is not None else value).rsplit(".", 1)[-1]


def _main_preview_contract_passed(result: dict[str, Any]) -> bool:
    media = result.get("media", {})
    return (
        int(result["advanced_playback_ms"]) >= int(result["requested_playback_ms"])
        and int(media.get("play_starts", 0)) > 0
        and int(media.get("video_frames", 0)) > 0
        and media.get("first_video_frame_ms") is not None
    )


def _playback_follow_contract_passed(result: dict[str, Any]) -> bool:
    selected_indices = [int(index) for index in result.get("selected_indices", [])]
    return (
        int(result["advanced_playback_ms"]) >= int(result["requested_playback_ms"])
        and len(set(selected_indices)) >= 2
        and int(result["final_selected_index"]) > int(result["initial_selected_index"])
        and float(result["timeline_viewport_after_x"]) > float(result["timeline_viewport_before_x"]) + 0.5
    )


def _short_visual_update_contract_passed(result: dict[str, Any]) -> bool:
    media = result.get("media", {})
    return (
        media.get("source_changes", 0) == 0
        and media.get("loading_transitions", 0) == 0
        and media.get("stops", 0) == 0
        and media.get("play_starts", 0) == 0
        and result["playback_state_after"] == "PlayingState"
        and int(result["position_after_ms"]) >= int(result["position_before_ms"])
    )


class InstrumentedEditBayBackend(EditBayBackend):
    """Test-only backend that counts calls crossing the QML/Python boundary."""

    def __init__(self, argv: list[str], workspace_root: Path) -> None:
        self.gui_boundary_calls: Counter[str] = Counter()
        self.gui_diagnostics: Counter[str] = Counter()
        self.qml_select_segment_arguments: list[int] = []
        super().__init__(argv, workspace_root=workspace_root)

    def reset_gui_diagnostics(self) -> None:
        self.gui_boundary_calls.clear()
        self.gui_diagnostics.clear()
        self.qml_select_segment_arguments.clear()

    @Property("QVariantList", notify=EditBayBackend.segmentsChanged)
    def subtitleSegments(self) -> list[dict[str, Any]]:
        self.gui_boundary_calls["property.subtitleSegments"] += 1
        self.gui_diagnostics["full_segment_materializations"] += 1
        if self._project is None:
            return []
        return deepcopy(self._project.get("segments", []))

    @Property("QVariantList", notify=EditBayBackend.shortVideoChanged)
    def shortVideoClips(self) -> list[dict[str, Any]]:
        self.gui_boundary_calls["property.shortVideoClips"] += 1
        self.gui_diagnostics["full_clip_materializations"] += 1
        return [
            self._build_short_video_clip_view(clip, index) for index, clip in enumerate(self._raw_short_video_clips())
        ]

    def _raw_short_video_clips(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        section = self._project.get("short_video", {})
        clips = section.get("clips", []) if isinstance(section, dict) else []
        return clips if isinstance(clips, list) else []

    def _segment_view(
        self,
        segment: dict[str, Any],
        source_index: int | None = None,
    ) -> dict[str, Any]:
        self.gui_diagnostics["segment_views"] += 1
        return super()._segment_view(segment, source_index)

    def _preview_text_for_segment(self, segment: dict[str, Any]) -> str:
        self.gui_diagnostics["preview_format_requests"] += 1
        cache = getattr(self, "_subtitle_preview_text_cache", {})
        segment_id = str(segment.get("id", ""))
        signature_builder = getattr(self, "_subtitle_preview_signature", None)
        signature = signature_builder(segment) if callable(signature_builder) else None
        cached = cache.get(segment_id) if isinstance(cache, dict) else None
        if cached is None or signature is None or cached[0] != signature:
            self.gui_diagnostics["preview_format_cache_misses"] += 1
        resolver = getattr(super(), "_preview_text_for_segment", None)
        if callable(resolver):
            return resolver(segment)
        from src.subtitle_line_count import segment_preview_text

        return segment_preview_text(segment)

    def _build_short_video_clip_view(
        self,
        clip: dict[str, Any],
        index: int,
    ) -> dict[str, Any]:
        self.gui_diagnostics["short_clip_materializations"] += 1
        return super()._build_short_video_clip_view(clip, index)

    @Slot(str, result=bool)
    def selectEditMode(self, mode: str) -> bool:
        self.gui_boundary_calls["selectEditMode"] += 1
        return super().selectEditMode(mode)

    @Slot(int, str, result=bool)
    def setEditorPlayhead(self, position_ms: int, basis: str) -> bool:
        self.gui_boundary_calls["setEditorPlayhead"] += 1
        return super().setEditorPlayhead(position_ms, basis)

    @Slot(int, result="QVariantMap")
    def shortVideoClipAt(self, index: int) -> dict[str, Any]:
        self.gui_boundary_calls["shortVideoClipAt"] += 1
        resolver = getattr(super(), "shortVideoClipAt", None)
        if callable(resolver):
            return resolver(index)
        clips = self._raw_short_video_clips()
        if not 0 <= index < len(clips):
            return {}
        return self._build_short_video_clip_view(clips[index], index)

    @Slot(int, result="QVariantMap")
    def segmentAt(self, index: int) -> dict[str, Any]:
        self.gui_boundary_calls["segmentAt"] += 1
        return super().segmentAt(index)

    @Slot(int, str, result=str)
    def formatSubtitlePreview(self, index: int, text: str) -> str:
        self.gui_boundary_calls["formatSubtitlePreview"] += 1
        return super().formatSubtitlePreview(index, text)

    @Slot(float, result="QVariantList")
    def activeSubtitleSegments(self, seconds: float) -> list[dict[str, Any]]:
        self.gui_boundary_calls["activeSubtitleSegments"] += 1
        return super().activeSubtitleSegments(seconds)

    @Slot(float, float, result="QVariantList")
    def visibleSubtitleSegments(self, start: float, end: float) -> list[dict[str, Any]]:
        self.gui_boundary_calls["visibleSubtitleSegments"] += 1
        return super().visibleSubtitleSegments(start, end)

    @Slot(int)
    def selectSegment(self, index: int) -> None:
        self.gui_boundary_calls["selectSegment"] += 1
        self.qml_select_segment_arguments.append(index)
        super().selectSegment(index)

    @Slot(float)
    def selectSegmentAtTime(self, seconds: float) -> None:
        self.gui_boundary_calls["selectSegmentAtTime"] += 1
        super().selectSegmentAtTime(seconds)

    @Slot(int, "QVariantMap")
    def updateSegment(self, index: int, changes: dict[str, Any]) -> None:
        self.gui_boundary_calls["updateSegment"] += 1
        super().updateSegment(index, changes)

    @Slot(int, "QVariantMap", result=bool)
    def updateShortVideoClip(self, index: int, fields: dict[str, Any]) -> bool:
        self.gui_boundary_calls["updateShortVideoClip"] += 1
        return super().updateShortVideoClip(index, fields)

    @Slot(int, int, result=bool)
    def moveShortVideoClip(self, from_index: int, to_index: int) -> bool:
        self.gui_boundary_calls["moveShortVideoClip"] += 1
        return super().moveShortVideoClip(from_index, to_index)

    @Slot(int, result=bool)
    def removeShortVideoClip(self, index: int) -> bool:
        self.gui_boundary_calls["removeShortVideoClip"] += 1
        return super().removeShortVideoClip(index)

    @Slot(str, result=bool)
    def setShortVideoGlobalFit(self, fit: str) -> bool:
        self.gui_boundary_calls["setShortVideoGlobalFit"] += 1
        return super().setShortVideoGlobalFit(fit)

    @Slot(str, result=bool)
    def setShortVideoGlobalBackgroundColor(self, color: str) -> bool:
        self.gui_boundary_calls["setShortVideoGlobalBackgroundColor"] += 1
        return super().setShortVideoGlobalBackgroundColor(color)

    @Slot(str, float, result=bool)
    def setShortVideoTransition(self, transition_type: str, duration: float) -> bool:
        self.gui_boundary_calls["setShortVideoTransition"] += 1
        return super().setShortVideoTransition(transition_type, duration)

    @Slot(float, result=bool)
    def setShortVideoSubtitleScale(self, percent: float) -> bool:
        self.gui_boundary_calls["setShortVideoSubtitleScale"] += 1
        return super().setShortVideoSubtitleScale(percent)


class GuiPerformanceScenarioRunner:
    def __init__(
        self,
        project_path: Path,
        *,
        playback_seconds: float,
        settle_ms: int,
    ) -> None:
        self.project_path = project_path.resolve()
        self.playback_seconds = playback_seconds
        self.settle_ms = settle_ms
        self.revision = _repository_revision()
        self._workspace = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        with patch("src.gui.CodexChatController.connect"):
            self.backend = InstrumentedEditBayBackend(
                ["subtitle-edit-bay-gui-performance"],
                Path(self._workspace.name),
            )
        self.harness = GuiTestHarness(
            self.backend,
            backend=self.backend,
            qml_roots=(QML_PATH.parent,),
            qml_message_allowlist=_comparison_qml_message_allowlist(self.revision),
        )
        self.window: QObject | None = None
        self.main_player: QMediaPlayer | None = None
        self.main_media_probe: MediaPlayerSignalProbe | None = None
        self.scenarios: list[dict[str, Any]] = []
        self.contracts: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        try:
            self._run_project_open()
            self._run_continuous_playback()
            self._run_editor_open_close()
            self._open_editor()
            self._run_selection_navigation()
            self._run_subtitle_edit()
            self._run_playback_follow()
            self._close_editor()
            self._run_short_mode_operations()
            self._run_short_visual_update()
            self._close_short_mode()
            self._check_materialization_contract()
            failed = [contract for contract in self.contracts if not contract["passed"]]
            return {
                "metadata": self._metadata(),
                "segment_count": self.backend.segmentCount,
                "scenarios": self.scenarios,
                "contracts": self.contracts,
                "contracts_passed": not failed,
                "peak_rss_bytes": _peak_resident_set_bytes(),
            }
        finally:
            self.close()

    def _metadata(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pyside": pyside_version,
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "quick_backend": os.environ.get("QT_QUICK_BACKEND", ""),
        }

    def _measure(
        self,
        name: str,
        action: Callable[[], dict[str, Any] | None],
        *,
        media_probes: tuple[MediaPlayerSignalProbe, ...] = (),
    ) -> dict[str, Any]:
        self.backend.reset_gui_diagnostics()
        for probe in media_probes:
            probe.reset()
        latency_probe = EventLoopLatencyProbe(interval_ms=10, parent=self.backend)
        latency_probe.start()
        started = time.perf_counter()
        details = action() or {}
        self.harness.process_events()
        action_elapsed_ms = (time.perf_counter() - started) * 1_000
        self.harness.wait(self.settle_ms)
        total_elapsed_ms = (time.perf_counter() - started) * 1_000
        latency = latency_probe.stop()
        media = self._combine_media_probes(media_probes)
        result = {
            "name": name,
            "action_elapsed_ms": round(action_elapsed_ms, 3),
            "settled_elapsed_ms": round(total_elapsed_ms, 3),
            "event_loop_latency_ms": latency.as_dict(),
            "python_qml_calls": dict(sorted(self.backend.gui_boundary_calls.items())),
            "diagnostic_counts": dict(sorted(self.backend.gui_diagnostics.items())),
            "media": media,
            "peak_rss_bytes": _peak_resident_set_bytes(),
            **details,
        }
        self.scenarios.append(result)
        return result

    @staticmethod
    def _combine_media_probes(
        probes: tuple[MediaPlayerSignalProbe, ...],
    ) -> dict[str, object]:
        totals: Counter[str] = Counter()
        players: list[dict[str, object]] = []
        first_video_frames_ms: list[float] = []
        for probe in probes:
            snapshot = probe.as_dict()
            players.append(
                {
                    "object_name": probe.player.objectName(),
                    **snapshot,
                }
            )
            for key, value in snapshot.items():
                if isinstance(value, int):
                    totals[key] += value
            first_video_frame_ms = snapshot.get("first_video_frame_ms")
            if isinstance(first_video_frame_ms, (int, float)):
                first_video_frames_ms.append(float(first_video_frame_ms))
        return {
            **dict(totals),
            "first_video_frame_ms": (round(min(first_video_frames_ms), 3) if first_video_frames_ms else None),
            "players": players,
        }

    def _run_project_open(self) -> None:
        def action() -> dict[str, Any]:
            if not self.backend._load_project_path(self.project_path, update_sources=True):
                raise AssertionError(self.backend.status)
            self.backend.autosave_timer.stop()
            _engine, self.window = self.harness.load_qml(QML_PATH, width=1_520, height=940)
            self.harness.wait_until(
                lambda: self.window is not None and self.window.findChild(QQuickItem, "mainWorkspace") is not None,
                description="main workflow to become interactive",
                timeout_ms=15_000,
            )
            self.main_player = self._find_main_player()
            self._mute_player(self.main_player)
            self._wait_for_media(self.main_player)
            self.main_media_probe = MediaPlayerSignalProbe(self.main_player)
            return {
                "media_player_instances": len(self._media_players()),
                "model_rows": self.backend._subtitle_model.rowCount(),
            }

        self._measure("project_initial_interactive", action)

    def _run_continuous_playback(self) -> None:
        player, media_probe = self._main_player_and_probe()

        def action() -> dict[str, Any]:
            player.setPosition(0)
            player.play()
            target_position = round(self.playback_seconds * 1_000)
            deadline = time.monotonic() + self.playback_seconds + 15.0
            playhead_lag: list[float] = []
            while player.position() < target_position:
                if _state_name(player.error()) != "NoError":
                    raise AssertionError(f"main preview playback failed: {player.errorString()}")
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        f"main preview reached only {player.position()} ms; expected {target_position} ms"
                    )
                self.harness.wait(25)
                editor_playhead = self.backend.editorPlayhead
                playhead_lag.append(abs(float(editor_playhead["sourcePositionMs"]) - player.position()))
            player.pause()
            return {
                "requested_playback_ms": target_position,
                "advanced_playback_ms": player.position(),
                "ui_playhead_lag_ms": summarize_durations_ms(playhead_lag).as_dict(),
            }

        result = self._measure(
            "main_preview_continuous_playback",
            action,
            media_probes=(media_probe,),
        )
        self._contract(
            "main_preview_decodes_and_advances",
            _main_preview_contract_passed(result),
            (
                f"advanced={result['advanced_playback_ms']} ms, "
                f"requested={result['requested_playback_ms']} ms, "
                f"play_starts={result['media'].get('play_starts', 0)}, "
                f"video_frames={result['media'].get('video_frames', 0)}, "
                f"first_frame_ms={result['media'].get('first_video_frame_ms')}"
            ),
        )

    def _run_editor_open_close(self) -> None:
        _player, media_probe = self._main_player_and_probe()
        before_players = len(self._media_players())

        def action() -> dict[str, Any]:
            self._open_editor()
            open_player_count = len(self._media_players())
            self._close_editor()
            return {
                "media_player_instances_before": before_players,
                "media_player_instances_while_open": open_player_count,
                "media_player_instances_after": len(self._media_players()),
            }

        result = self._measure(
            "editor_open_close_without_media_reload",
            action,
            media_probes=(media_probe,),
        )
        media = result["media"]
        self._contract(
            "editor_keeps_media_source",
            media.get("source_changes", 0) == 0
            and media.get("loading_transitions", 0) == 0
            and result["media_player_instances_while_open"] == before_players
            and result["media_player_instances_after"] == before_players,
            (
                f"source_changes={media.get('source_changes', 0)}, "
                f"loading={media.get('loading_transitions', 0)}, "
                f"players={before_players}->{result['media_player_instances_while_open']}"
                f"->{result['media_player_instances_after']}"
            ),
        )

    def _run_selection_navigation(self) -> None:
        window = self._window()
        caption_table = self.harness.find_item(window, "captionTable")
        timeline = self.harness.find_item(window, "editorTimeline")
        segment_count = self.backend.segmentCount
        indices = [0, segment_count // 2, segment_count - 1]

        def action() -> dict[str, Any]:
            caption_delegate_counts: list[int] = []
            timeline_delegate_counts: list[int] = []
            list_selections: list[int] = []
            timeline_selections: list[int] = []
            for index in indices:
                caption_delegate = self._caption_delegate(caption_table, index)
                list_calls_before = len(self.backend.qml_select_segment_arguments)
                self.harness.click_at(
                    window,
                    caption_delegate,
                    2.0,
                    caption_delegate.height() / 2,
                )
                list_selections.append(
                    index
                    if index in self.backend.qml_select_segment_arguments[list_calls_before:]
                    else -1
                )

                timeline_delegate = self._timeline_delegate(timeline, index)
                timeline_calls_before = len(self.backend.qml_select_segment_arguments)
                self.harness.click(window, timeline_delegate)
                timeline_selections.append(
                    index
                    if index in self.backend.qml_select_segment_arguments[timeline_calls_before:]
                    else -1
                )
                caption_delegate_counts.append(
                    len(
                        self.harness.visual_items_with_properties(
                            caption_table,
                            "segmentId",
                            "editorText",
                            "subtitleFontScale",
                        )
                    )
                )
                timeline_delegate_counts.append(
                    len(
                        self.harness.visual_items_with_properties(
                            timeline,
                            "sourceIndex",
                            "segment",
                            "originalX",
                            "originalWidth",
                        )
                    )
                )
            return {
                "list_selected_indices": list_selections,
                "timeline_selected_indices": timeline_selections,
                "caption_delegate_count_max": max(caption_delegate_counts, default=0),
                "timeline_delegate_count_max": max(timeline_delegate_counts, default=0),
                "model_rows": segment_count,
            }

        result = self._measure("list_and_timeline_selection", action)
        delegate_max = max(
            int(result["caption_delegate_count_max"]),
            int(result["timeline_delegate_count_max"]),
        )
        self._contract(
            "subtitle_views_are_virtualized",
            delegate_max > 0 and delegate_max < min(segment_count, 100),
            f"delegates={delegate_max}, model_rows={segment_count}",
        )
        self._contract(
            "list_and_timeline_controls_select_requested_rows",
            result["list_selected_indices"] == indices and result["timeline_selected_indices"] == indices,
            (
                f"list={result['list_selected_indices']}, "
                f"timeline={result['timeline_selected_indices']}, expected={indices}"
            ),
        )

    def _run_subtitle_edit(self) -> None:
        index = self.backend.segmentCount // 2
        current = dict(self.backend._project["segments"][index])
        expected_text = f"{current['text']} performance-edit"
        expected_start = float(current["start"]) + 0.01
        expected_end = float(current["end"]) + 0.02
        font_choices = self.backend.fontChoices
        expected_font = next(
            (
                str(font["family"])
                for font in font_choices
                if str(font["family"]) != str(current.get("subtitle_font_family", ""))
            ),
            str(font_choices[0]["family"]),
        )

        def action() -> dict[str, Any]:
            caption_table = self.harness.find_item(self._window(), "captionTable")
            delegate = self._caption_delegate(caption_table, index)

            time_fields = [
                item
                for item in self.harness.visual_items_with_properties(delegate, "validator", "background", "text")
                if item.metaObject().className() == "TimeField"
                if callable(getattr(getattr(item, "editingFinished", None), "emit", None))
            ]
            time_fields.sort(key=lambda item: item.x())
            if len(time_fields) != 2:
                raise AssertionError(f"Expected two caption time fields, found {len(time_fields)}")
            start_field, end_field = time_fields

            text_area = self.harness.find_visual_item(delegate, "captionTextArea")
            text_area.forceActiveFocus()
            self.harness.set_property(text_area, "text", expected_text)
            start_field.forceActiveFocus()
            self.harness.process_events()

            self._commit_text_field(start_field, f"{expected_start:.3f}")
            self._commit_text_field(end_field, f"{expected_end:.3f}")

            combos = [
                item
                for item in self.harness.visual_items(delegate)
                if callable(getattr(getattr(item, "activated", None), "emit", None))
                and item.metaObject().indexOfProperty("currentValue") >= 0
            ]
            font_combo = next((item for item in combos if item.objectName() == "captionFontCombo"), None)
            speaker_combo = next((item for item in combos if item is not font_combo), None)
            if font_combo is None or speaker_combo is None:
                raise AssertionError("Could not resolve caption speaker/font controls")
            speaker_index = next(
                speaker_index
                for speaker_index, speaker in enumerate(self.backend.projectSpeakers)
                if speaker["style"] == "Speaker_Carol"
            )
            font_index = next(
                font_index for font_index, font in enumerate(font_choices) if font["family"] == expected_font
            )
            self._activate_combo(speaker_combo, speaker_index)
            self._activate_combo(font_combo, font_index)

            size_spin = self._find_control_with_signal(
                delegate,
                "valueModified",
                object_name="captionSizeSpin",
            )
            self.harness.set_property(size_spin, "value", 125)
            self.harness.emit_signal(size_spin, "valueModified")

            self.backend.autosave_timer.stop()
            updated = self.backend._project["segments"][index]
            return {
                "edited_index": index,
                "selected_index_after": self.backend.selectedSegmentIndex,
                "updated_text": str(updated["text"]),
                "updated_start": float(updated["start"]),
                "updated_end": float(updated["end"]),
                "updated_speaker": str(updated["speaker"]),
                "updated_font": str(updated["subtitle_font_family"]),
                "updated_scale": float(updated["subtitle_font_scale"]),
            }

        result = self._measure("subtitle_text_time_speaker_font_edit", action)
        self._contract(
            "combined_subtitle_edit_is_applied",
            result["edited_index"] == index
            and result["selected_index_after"] == index
            and result["updated_text"] == expected_text
            and abs(float(result["updated_start"]) - expected_start) < 0.001
            and abs(float(result["updated_end"]) - expected_end) < 0.001
            and result["updated_speaker"] == "Speaker_Carol"
            and result["updated_font"] == expected_font
            and result["updated_scale"] == 1.25
            and int(result["python_qml_calls"].get("updateSegment", 0)) >= 6,
            (
                f"edited_index={result['edited_index']}, expected={index}, "
                f"time={result['updated_start']:.3f}-{result['updated_end']:.3f}, "
                f"update_calls={result['python_qml_calls'].get('updateSegment', 0)}"
            ),
        )

    def _run_playback_follow(self) -> None:
        player, media_probe = self._editor_player_and_probe()
        follow_seconds = min(3.0, max(1.0, self.playback_seconds / 10.0))
        timeline = self.harness.find_item(self._window(), "editorTimeline")
        start_position_ms = min(
            12_000,
            max(0, player.duration() - round(follow_seconds * 1_000) - 1_000),
        )
        player.pause()
        player.setPosition(start_position_ms)
        self.harness.wait_until(
            lambda: abs(player.position() - start_position_ms) <= 250,
            description="editor playback-follow start position",
            timeout_ms=5_000,
        )
        start_seconds = start_position_ms / 1_000
        active_indices = [
            index
            for index, segment in enumerate(self.backend._project["segments"])
            if float(segment["start"]) <= start_seconds <= float(segment["end"])
        ]
        expected_initial_index = active_indices[-1]
        self.harness.wait_until(
            lambda: self.backend.selectedSegmentIndex == expected_initial_index,
            description="initial subtitle selection before playback-follow measurement",
            timeout_ms=5_000,
        )
        self.harness.set_property(timeline, "viewportX", 0.0)
        initial_selected_index = self.backend.selectedSegmentIndex
        timeline_before_x = float(timeline.property("viewportX"))
        selected_indices = [initial_selected_index]

        def selection_changed() -> None:
            selected_indices.append(self.backend.selectedSegmentIndex)

        self.backend.selectionChanged.connect(selection_changed)

        def action() -> dict[str, Any]:
            player.play()
            target_position = start_position_ms + round(follow_seconds * 1_000)
            deadline = time.monotonic() + follow_seconds + 10.0
            playhead_lag: list[float] = []
            while player.position() < target_position:
                if _state_name(player.error()) != "NoError":
                    raise AssertionError(f"editor preview playback failed: {player.errorString()}")
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        f"editor playback reached only {player.position()} ms; expected {target_position} ms"
                    )
                self.harness.wait(25)
                playhead_lag.append(abs(float(self.backend.editorPlayhead["sourcePositionMs"]) - player.position()))
            player.pause()
            timeline = self.harness.find_item(self._window(), "editorTimeline")
            return {
                "requested_playback_ms": target_position,
                "advanced_playback_ms": player.position(),
                "initial_selected_index": initial_selected_index,
                "final_selected_index": self.backend.selectedSegmentIndex,
                "selected_indices": list(selected_indices),
                "timeline_viewport_before_x": timeline_before_x,
                "timeline_viewport_after_x": float(timeline.property("viewportX")),
                "ui_playhead_lag_ms": summarize_durations_ms(playhead_lag).as_dict(),
            }

        try:
            result = self._measure(
                "playback_selection_and_timeline_follow",
                action,
                media_probes=(media_probe,),
            )
        finally:
            self.backend.selectionChanged.disconnect(selection_changed)
        self._contract(
            "editor_playback_follow_advances",
            _playback_follow_contract_passed(result),
            (
                f"advanced={result['advanced_playback_ms']} ms, "
                f"selected={result['selected_indices']}, "
                f"timeline={result['timeline_viewport_before_x']:.1f}"
                f"->{result['timeline_viewport_after_x']:.1f}"
            ),
        )

    def _run_short_mode_operations(self) -> None:
        window = self._window()

        def action() -> dict[str, Any]:
            self._open_short_mode()
            screen = self.harness.find_item(window, "shortModeScreen")
            clip_list = self.harness.find_item(window, "shortModeClipList")
            clip_list_view = self.harness.find_item(window, "shortModeClipListView")
            clip_count = self._short_clip_count()
            selected_indices: list[int] = []
            for index in (0, clip_count // 2, clip_count - 1):
                clip_delegate = self._short_clip_delegate(clip_list_view, index)
                if not clip_delegate.isVisible():
                    raise AssertionError(f"Short clip delegate {index} is not instantiated")
                self.harness.emit_signal(clip_list, "selected", index)
                self.harness.wait_until(
                    lambda index=index: int(screen.property("currentClipIndex")) == index,
                    description=f"short clip selection {index}",
                    timeout_ms=5_000,
                )
                selected_indices.append(int(screen.property("currentClipIndex")))

            middle = clip_count // 2
            middle_delegate = self._short_clip_delegate(clip_list_view, middle)
            original_middle_clip = dict(self.backend._project["short_video"]["clips"][middle])
            expected_start = float(original_middle_clip["start"]) + 0.01
            expected_end = float(original_middle_clip["end"]) - 0.01
            start_field = self.harness.find_visual_item(
                middle_delegate,
                f"shortModeStartTimeField{middle}",
            )
            end_field = self.harness.find_visual_item(
                middle_delegate,
                f"shortModeEndTimeField{middle}",
            )
            self._commit_text_field(start_field, f"{expected_start:.3f}")
            self._commit_text_field(end_field, f"{expected_end:.3f}")
            trimmed_clip = self.backend._project["short_video"]["clips"][middle]
            trimmed = (
                abs(float(trimmed_clip["start"]) - expected_start) < 0.001
                and abs(float(trimmed_clip["end"]) - expected_end) < 0.001
            )

            move_down_button = self._find_control_with_signal(
                middle_delegate,
                "clicked",
                text="▼",
            )
            self.harness.emit_signal(move_down_button, "clicked")
            moved = (
                self.backend._project["short_video"]["clips"][middle + 1]["segment_id"]
                == original_middle_clip["segment_id"]
            )

            last_index = self._short_clip_count() - 1
            removed_segment_id = self.backend._project["short_video"]["clips"][last_index]["segment_id"]
            last_delegate = self._short_clip_delegate(clip_list_view, last_index)
            remove_button = self._find_control_with_signal(
                last_delegate,
                "clicked",
                text="✕",
            )
            count_before_remove = self._short_clip_count()
            self.harness.emit_signal(remove_button, "clicked")
            removed = self._short_clip_count() == count_before_remove - 1 and all(
                clip["segment_id"] != removed_segment_id for clip in self.backend._project["short_video"]["clips"]
            )

            transition_slider = self.harness.find_item(window, "shortModeTransitionDurationSlider")
            self.harness.set_property(transition_slider, "value", 0.3)
            transition_combo = self.harness.find_item(window, "shortModeTransitionCombo")
            self._activate_combo(transition_combo, 1)
            transition = self.backend._project["short_video"]["transition"]
            settings_changed = transition["type"] == "fade" and abs(float(transition["duration"]) - 0.3) < 0.001

            self.backend.autosave_timer.stop()
            first_delegate = self._short_clip_delegate(clip_list_view, 0)
            if not first_delegate.isVisible():
                raise AssertionError("First short clip delegate is not instantiated")
            self.harness.emit_signal(clip_list, "selected", 0)
            self.harness.wait_until(
                lambda: int(screen.property("currentClipIndex")) == 0,
                description="first short clip selection",
                timeout_ms=5_000,
            )
            return {
                "selected_indices": selected_indices,
                "expected_selected_indices": [0, middle, clip_count - 1],
                "move_accepted": moved,
                "trim_accepted": trimmed,
                "remove_accepted": removed,
                "settings_accepted": settings_changed,
                "clip_rows": self._short_clip_count(),
                "short_delegate_count": self.harness.count_visual_items(
                    window,
                    object_name_prefix="shortModeClipItem",
                ),
                "media_player_instances": len(self._media_players()),
            }

        result = self._measure("short_mode_selection_reorder_delete_settings", action)
        self._contract(
            "short_clip_view_is_virtualized",
            0 < int(result["short_delegate_count"]) < min(int(result["clip_rows"]), 100),
            f"delegates={result['short_delegate_count']}, rows={result['clip_rows']}",
        )
        self._contract(
            "short_mode_operations_apply",
            result["selected_indices"] == result["expected_selected_indices"]
            and all(
                bool(result[key])
                for key in (
                    "move_accepted",
                    "trim_accepted",
                    "remove_accepted",
                    "settings_accepted",
                )
            ),
            (
                f"selected={result['selected_indices']}, "
                f"move={result['move_accepted']}, trim={result['trim_accepted']}, "
                f"remove={result['remove_accepted']}, settings={result['settings_accepted']}"
            ),
        )

    def _run_short_visual_update(self) -> None:
        player = self._find_short_player()
        self._mute_player(player)
        self._wait_for_media(player)
        player.setPosition(0)
        player.play()
        self.harness.wait_until(
            lambda: player.position() > 500,
            description="short preview playback to advance",
            timeout_ms=10_000,
        )
        probe = MediaPlayerSignalProbe(player)
        before_position = player.position()

        def action() -> dict[str, Any]:
            window = self._window()
            clip_list_view = self.harness.find_item(window, "shortModeClipListView")
            first_delegate = self._short_clip_delegate(clip_list_view, 0)
            fit_combo = self.harness.find_visual_item(first_delegate, "shortModeFitCombo0")
            self._activate_combo(fit_combo, 1)
            background_field = self.harness.find_item(window, "shortModeBackgroundColorField")
            self._commit_text_field(background_field, "102030")
            scale_spin = self.harness.find_item(window, "shortModeSubtitleScaleSpin")
            self.harness.set_property(scale_spin, "value", 165)
            self.harness.emit_signal(scale_spin, "valueModified")
            self.backend.autosave_timer.stop()
            clip = self.backend._project["short_video"]["clips"][0]
            settings = self.backend._project["short_video"]
            return {
                "clip_changed": clip.get("fit") == "contain",
                "background_changed": settings["global_background_color"] == "#102030",
                "scale_changed": settings["subtitle_scale_percent"] == 165,
                "position_before_ms": before_position,
                "position_after_ms": player.position(),
                "playback_state_after": _state_name(player.playbackState()),
            }

        result = self._measure(
            "short_visual_update_preserves_playback",
            action,
            media_probes=(probe,),
        )
        media = result["media"]
        self._contract(
            "short_visual_update_keeps_player",
            _short_visual_update_contract_passed(result)
            and bool(result["clip_changed"])
            and bool(result["background_changed"])
            and bool(result["scale_changed"]),
            (
                f"state={result['playback_state_after']}, "
                f"position={result['position_before_ms']}->{result['position_after_ms']}, "
                f"source_changes={media.get('source_changes', 0)}, "
                f"loading={media.get('loading_transitions', 0)}, "
                f"starts={media.get('play_starts', 0)}, stops={media.get('stops', 0)}"
            ),
        )
        player.pause()

    def _check_materialization_contract(self) -> None:
        full_segments = sum(
            int(scenario["diagnostic_counts"].get("full_segment_materializations", 0)) for scenario in self.scenarios
        )
        full_clips = sum(
            int(scenario["diagnostic_counts"].get("full_clip_materializations", 0)) for scenario in self.scenarios
        )
        self._contract(
            "qml_avoids_full_array_materialization",
            full_segments == 0 and full_clips == 0,
            f"full_segments={full_segments}, full_clips={full_clips}",
        )

    def _contract(self, name: str, passed: bool, evidence: str) -> None:
        self.contracts.append(
            {
                "name": name,
                "passed": bool(passed),
                "evidence": evidence,
            }
        )

    def _window(self) -> QObject:
        if self.window is None:
            raise RuntimeError("QML window is not loaded")
        return self.window

    def _media_players(self) -> list[QMediaPlayer]:
        return self._window().findChildren(QMediaPlayer)

    def _find_main_player(self) -> QMediaPlayer:
        window = self._window()
        named = window.findChild(QMediaPlayer, "mainPreviewPlayer")
        if named is not None:
            return named
        players = self._media_players()
        if not players:
            raise AssertionError("QML did not create a main MediaPlayer")
        return players[0]

    def _find_short_player(self) -> QMediaPlayer:
        window = self._window()
        named = window.findChild(QMediaPlayer, "shortPreviewPlayer")
        if named is not None:
            return named
        players = [player for player in self._media_players() if player is not self.main_player]
        if not players:
            raise AssertionError("QML did not create a short preview MediaPlayer")
        return players[-1]

    def _main_player_and_probe(self) -> tuple[QMediaPlayer, MediaPlayerSignalProbe]:
        if self.main_player is None or self.main_media_probe is None:
            raise RuntimeError("main media probe is not ready")
        return self.main_player, self.main_media_probe

    def _editor_player_and_probe(self) -> tuple[QMediaPlayer, MediaPlayerSignalProbe]:
        main_player, main_probe = self._main_player_and_probe()
        editor_players = [player for player in self._media_players() if player is not main_player]
        if not editor_players:
            main_probe.refresh_video_sink()
            return main_player, main_probe
        player = editor_players[-1]
        self._mute_player(player)
        self._wait_for_media(player)
        return player, MediaPlayerSignalProbe(player)

    @staticmethod
    def _mute_player(player: QMediaPlayer) -> None:
        output = player.audioOutput()
        if output is not None:
            output.setMuted(True)

    def _wait_for_media(self, player: QMediaPlayer) -> None:
        self.harness.wait_until(
            lambda: player.duration() > 0 or _state_name(player.error()) != "NoError",
            description=f"media metadata for {player.objectName() or 'QML player'}",
            timeout_ms=15_000,
            interval_ms=25,
        )
        if _state_name(player.error()) != "NoError":
            raise AssertionError(f"media failed to load: {player.errorString()}")

    def _open_editor(self) -> None:
        if self._window().property("activeOverlay") == "editor":
            return
        self.harness.click(
            self._window(),
            self.harness.find_item(self._window(), "editSubtitlesButton"),
        )
        self.harness.wait_until(
            lambda: (
                self._window().findChild(QQuickItem, "captionTable") is not None
                and self._window().findChild(QQuickItem, "captionTable").isVisible()
            ),
            description="subtitle editor to open",
            timeout_ms=15_000,
        )

    def _close_editor(self) -> None:
        back = self._window().findChild(QQuickItem, "editorBackButton")
        if back is None or not back.isVisible():
            return
        self.harness.click(self._window(), back)
        self.harness.wait_until(
            lambda: self._window().property("activeOverlay") != "editor",
            description="subtitle editor to close",
            timeout_ms=5_000,
        )

    def _open_short_mode(self) -> None:
        if self._window().property("activeOverlay") == "short":
            return
        self.harness.click(
            self._window(),
            self.harness.find_item(self._window(), "shortModeOpenButton"),
        )
        self.harness.wait_until(
            lambda: (
                self._window().findChild(QQuickItem, "shortModeScreen") is not None
                and self._window().findChild(QQuickItem, "shortModeScreen").isVisible()
            ),
            description="short mode to open",
            timeout_ms=15_000,
        )

    def _close_short_mode(self) -> None:
        back = self._window().findChild(QQuickItem, "shortModeBackButton")
        if back is None or not back.isVisible():
            return
        # Release the decoder before the Loader destroys the short preview.
        # Older comparison revisions can otherwise block inside QtMultimedia
        # teardown, which is outside every measured scenario.
        short_player = self._find_short_player()
        short_player.stop()
        short_player.setSource(QUrl())
        self.harness.wait(100)
        self.harness.click(self._window(), back)
        self.harness.wait_until(
            lambda: self._window().property("activeOverlay") != "short",
            description="short mode to close",
            timeout_ms=5_000,
        )

    def _short_clip_count(self) -> int:
        counter = getattr(self.backend, "shortVideoClipCount", None)
        return int(counter) if counter is not None else len(self.backend._raw_short_video_clips())

    def _find_semantic_item(
        self,
        root: QObject,
        expected: dict[str, object],
        *,
        required_properties: tuple[str, ...] = (),
        description: str,
    ) -> QQuickItem:
        property_names = (*required_properties, *expected)

        def find() -> QQuickItem | None:
            for item in self.harness.visual_items_with_properties(root, *property_names):
                if all(item.property(name) == value for name, value in expected.items()):
                    return item
            return None

        self.harness.wait_until(
            lambda: find() is not None,
            description=description,
            timeout_ms=5_000,
        )
        return self.harness.find_visual_item_by_properties(
            root,
            expected,
            required_properties=required_properties,
        )

    def _caption_delegate(self, caption_table: QQuickItem, index: int) -> QQuickItem:
        target_y = min(
            max(0.0, float(caption_table.property("contentHeight")) - caption_table.height()),
            index * 127.0,
        )
        self.harness.set_property(caption_table, "contentY", target_y)
        return self._find_semantic_item(
            caption_table,
            {"index": index},
            required_properties=("segmentId", "editorText", "subtitleFontScale"),
            description=f"caption delegate {index}",
        )

    def _timeline_delegate(self, timeline: QQuickItem, index: int) -> QQuickItem:
        start = float(self.backend._project["segments"][index]["start"])
        viewport_x = max(0.0, start * float(timeline.property("pixelsPerSecond")) - 120.0)
        self.harness.set_property(timeline, "viewportX", viewport_x)
        delegate = self._find_semantic_item(
            timeline,
            {"sourceIndex": index},
            required_properties=("modelData", "segment", "originalX", "originalWidth"),
            description=f"timeline delegate {index}",
        )
        segment = _variant(delegate.property("segment"))
        if not isinstance(segment, dict) or not segment:
            # b600e90 expects an obsolete nested shape. Normalize only the
            # instantiated reference delegate so both revisions can execute
            # the same click path without changing product-side timings.
            model_data = _variant(delegate.property("modelData"))
            if isinstance(model_data, dict) and model_data:
                self.harness.set_property(delegate, "segment", model_data)
        self.harness.wait_until(
            delegate.isVisible,
            description=f"visible timeline delegate {index}",
            timeout_ms=5_000,
        )
        return delegate

    def _short_clip_delegate(self, clip_list_view: QQuickItem, index: int) -> QQuickItem:
        target_y = min(
            max(0.0, float(clip_list_view.property("contentHeight")) - clip_list_view.height()),
            index * 130.0,
        )
        self.harness.set_property(clip_list_view, "contentY", target_y)
        self.harness.wait_until(
            lambda: any(
                item.objectName() == f"shortModeClipItem{index}" for item in self.harness.visual_items(clip_list_view)
            ),
            description=f"short clip delegate {index}",
            timeout_ms=5_000,
        )
        return self.harness.find_visual_item(clip_list_view, f"shortModeClipItem{index}")

    def _find_control_with_signal(
        self,
        root: QObject,
        signal_name: str,
        *,
        object_name: str | None = None,
        text: str | None = None,
        required_properties: tuple[str, ...] = (),
    ) -> QQuickItem:
        for item in self.harness.visual_items(root):
            if object_name is not None and item.objectName() != object_name:
                continue
            if text is not None and str(item.property("text")) != text:
                continue
            if not all(item.metaObject().indexOfProperty(name) >= 0 for name in required_properties):
                continue
            if callable(getattr(getattr(item, signal_name, None), "emit", None)):
                return item
        raise AssertionError(
            f"Could not find QML control signal={signal_name!r}, objectName={object_name!r}, text={text!r}"
        )

    def _commit_text_field(self, field: QQuickItem, text: str) -> None:
        field.forceActiveFocus()
        self.harness.process_events()
        self.harness.set_property(field, "text", text)
        self.harness.emit_signal(field, "editingFinished")

    def _activate_combo(self, combo: QQuickItem, index: int) -> None:
        self.harness.set_property(combo, "currentIndex", index)
        self.harness.emit_signal(combo, "activated", index)

    def close(self) -> None:
        try:
            if self.main_player is not None:
                self.main_player.stop()
                self.main_player.setSource(QUrl())
            self.harness.cleanup()
        finally:
            self.backend.autosave_timer.stop()
            self.backend.elapsed_timer.stop()
            self.backend._audio_preview_level_timer.stop()
            self.backend._audio_master_mixer.stop()
            try:
                self.backend._codex_chat.shutdown()
            finally:
                self.backend._shutdown_executor()
                self._workspace.cleanup()
