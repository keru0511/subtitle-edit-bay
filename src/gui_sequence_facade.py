from __future__ import annotations

from typing import TYPE_CHECKING

import math
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    Property,
    Signal,
    Slot,
)
from PySide6.QtWidgets import QFileDialog

from .media_probe import probe_media_duration
from .subtitle_project import (
    SubtitleProjectError,
)
from .video_sequence import VideoSequence, VideoSequenceError

from .gui_feature_facade import FeatureFacade

if TYPE_CHECKING:
    from .gui import EditBayBackend


class SequenceFacade(FeatureFacade):
    """素材とシーケンス編集の画面窓口。"""

    sequenceChanged = Signal()

    def __init__(self, backend: "EditBayBackend") -> None:
        super().__init__(backend)
        backend.sequenceChanged.connect(self.sequenceChanged.emit)

    def _sequence_model_for_facade(self) -> VideoSequence | None:
        """Read the sequence through ProjectEditorController only.

        QML never receives the mutable project dictionary.  The controller
        validates the persisted payload and applies all mutations, while this
        facade builds a detached view for presentation.
        """

        backend = self._backend

        if backend._project is None or backend._project_editor_controller is None:
            return None
        try:
            return backend._project_editor_controller.sequence_model()
        except SubtitleProjectError:
            return None

    def _sequence_view_payload(self) -> dict[str, Any]:
        model = self._sequence_model_for_facade()
        if model is None:
            return {
                "schemaVersion": 1,
                "assets": [],
                "clips": [],
                "outputDuration": 0.0,
                "isLegacySingleVideo": False,
            }

        asset_clip_counts = {asset.id: sum(clip.asset_id == asset.id for clip in model.clips) for asset in model.assets}
        assets = [
            {
                "id": asset.id,
                "path": asset.path,
                "name": Path(asset.path).name,
                "duration": asset.duration_seconds,
                "clipCount": asset_clip_counts.get(asset.id, 0),
            }
            for asset in model.assets
        ]
        timeline = model.timeline if model.clips else None
        timeline_clips = timeline.clips if timeline is not None else ()
        assets_by_id = {asset.id: asset for asset in model.assets}
        clips: list[dict[str, Any]] = []
        for entry in timeline_clips:
            clip = entry.clip
            asset = assets_by_id[clip.asset_id]
            view = entry.as_view()
            view.update(
                {
                    "assetPath": asset.path,
                    "assetName": Path(asset.path).name,
                    "audioLinked": clip.audio_linked,
                    "volume": clip.volume,
                    "audioOffset": clip.audio_offset_seconds,
                    "muted": clip.muted,
                }
            )
            clips.append(view)
        return {
            "schemaVersion": model.schema_version,
            "assets": assets,
            "clips": clips,
            "outputDuration": timeline.total_duration if timeline is not None else 0.0,
            "isLegacySingleVideo": model.is_legacy_single_video(),
        }

    def _sequence_failure(self, message: str) -> bool:
        backend = self._backend
        backend._sequence_error = str(message)
        backend.sequenceChanged.emit()
        backend._set_status(backend._sequence_error, "CHECK")
        return False

    def _apply_sequence_mutation(
        self,
        mutation: Callable[[VideoSequence], VideoSequence],
        success_message: str,
    ) -> bool:
        backend = self._backend
        if backend._running:
            return self._sequence_failure("処理中はsequenceを変更できません")
        if backend._project is None or backend._project_editor_controller is None:
            return self._sequence_failure("先に編集プロジェクトを開いてください")
        backend._sequence_error = ""
        try:
            updated = backend._project_editor_controller.apply_sequence_mutation(mutation)
        except (SubtitleProjectError, VideoSequenceError, TypeError, ValueError) as error:
            return self._sequence_failure(f"sequenceを変更できません: {error}")
        if updated is None:
            return self._sequence_failure("sequenceを変更できません")
        backend._set_status(success_message, "EDIT")
        return True

    @Property("QVariantMap", notify=sequenceChanged)
    def sequenceView(self) -> dict[str, Any]:
        return deepcopy(self._sequence_view_payload())

    @Property("QVariantList", notify=sequenceChanged)
    def mediaBinAssets(self) -> list[dict[str, Any]]:
        return deepcopy(self._sequence_view_payload()["assets"])

    @Property("QVariantList", notify=sequenceChanged)
    def sequenceClips(self) -> list[dict[str, Any]]:
        return deepcopy(self._sequence_view_payload()["clips"])

    @Property(float, notify=sequenceChanged)
    def sequenceOutputDuration(self) -> float:
        return float(self._sequence_view_payload()["outputDuration"])

    @Property("QVariantMap", notify=sequenceChanged)
    def sequencePlayhead(self) -> dict[str, Any]:
        backend = self._backend
        model = self._sequence_model_for_facade()
        if model is None or not model.clips:
            return {
                "outputMs": 0,
                "outputSeconds": 0.0,
                "clipId": "",
                "sourceTime": 0.0,
            }
        timeline = model.timeline
        output_seconds = min(
            max(0.0, backend._sequence_playhead_seconds),
            timeline.total_duration,
        )
        # Keep output-to-source mapping in the #404 domain API.  QML only
        # renders this result and never reconstructs transition overlap.
        position = timeline.output_to_source_seconds(output_seconds)
        return {
            "outputMs": int(round(output_seconds * 1000)),
            "outputSeconds": output_seconds,
            "clipId": position.clip_id,
            "sourceTime": position.source_time,
        }

    @Property(str, notify=sequenceChanged)
    def sequenceError(self) -> str:
        backend = self._backend
        return backend._sequence_error

    @Slot(str, result=bool)
    def addSequenceAsset(self, path: str) -> bool:
        backend = self._backend
        if backend._running:
            return self._sequence_failure("処理中はsequence素材を変更できません")
        candidate = backend._local_path(path)
        try:
            candidate = candidate.expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return self._sequence_failure(f"動画素材を確認できません: {error}")
        if not candidate.is_file():
            return self._sequence_failure("動画素材ファイルが存在しません")
        supported, reason = backend._is_supported_media_file(
            candidate,
            {"video"},
            "sequence動画素材",
        )
        if not supported:
            return self._sequence_failure(reason or "動画素材として利用できません")
        model = self._sequence_model_for_facade()
        if model is None:
            return self._sequence_failure("sequenceを読み込めません")
        normalized = backend._normalized_source_path(str(candidate))
        if any(backend._normalized_source_path(asset.path) == normalized for asset in model.assets):
            return self._sequence_failure("同じ動画素材は既にmedia binにあります")
        try:
            duration = float(probe_media_duration(candidate))
        except (OSError, ValueError, TypeError, subprocess.CalledProcessError) as error:
            return self._sequence_failure(f"動画の長さを確認できません: {error}")
        if not math.isfinite(duration) or duration <= 0.0:
            return self._sequence_failure("動画の長さが不明なため追加できません")
        return self._apply_sequence_mutation(
            lambda sequence: sequence.add_asset(str(candidate), duration),
            f"media binへ動画を追加しました: {candidate.name}",
        )

    @Slot("QVariantList", result=int)
    def addSequenceAssets(self, paths: list[Any]) -> int:
        added = 0
        for path in paths or []:
            if self.addSequenceAsset(path):
                added += 1
        return added

    @Slot(result=str)
    def browseSequenceAsset(self) -> str:
        backend = self._backend
        if backend._running:
            self._sequence_failure("処理中はsequence素材を変更できません")
            return ""
        start_dir = str(backend.workspace_root)
        model = self._sequence_model_for_facade()
        if model and model.assets:
            start_dir = str(Path(model.assets[-1].path).parent)
        path, _ = QFileDialog.getOpenFileName(
            None,
            "sequenceへ追加する動画を選択",
            start_dir,
            "Video files (*.avi *.m2ts *.mkv *.mov *.mp4 *.mpeg *.mpg *.ts *.webm *.wmv);;All files (*)",
        )
        if path:
            self.addSequenceAsset(path)
        return path

    @Slot(str, result=bool)
    def addSequenceClip(self, asset_id: str) -> bool:
        return self.insertSequenceClip(asset_id, len(self.sequenceClips))

    @Slot(str, int, result=bool)
    def insertSequenceClip(self, asset_id: str, index: int) -> bool:
        model = self._sequence_model_for_facade()
        if model is None:
            return self._sequence_failure("sequenceを読み込めません")
        try:
            asset = next(asset for asset in model.assets if asset.id == str(asset_id))
        except StopIteration:
            return self._sequence_failure("指定されたsequence素材が見つかりません")
        if asset.duration_seconds <= 0.0:
            return self._sequence_failure("動画の長さが不明なためclipを追加できません")
        return self._apply_sequence_mutation(
            lambda sequence: sequence.add_clip(
                asset.id,
                0.0,
                asset.duration_seconds,
                index=index,
            ),
            f"sequenceへclipを追加しました: {asset.path}",
        )

    @Slot(str, int, result=bool)
    def moveSequenceClip(self, clip_id: str, index: int) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.reorder_clip(clip_id, index),
            "sequenceの順序を変更しました",
        )

    @Slot(str, result=bool)
    def removeSequenceClip(self, clip_id: str) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.remove_clip(clip_id),
            "sequenceからclipを削除しました",
        )

    @Slot(str, float, float, result=bool)
    def trimSequenceClip(self, clip_id: str, source_start: float, source_end: float) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.trim_clip(clip_id, source_start, source_end),
            "clipの範囲を更新しました",
        )

    @Slot(str, str, float, result=bool)
    def setSequenceTransition(self, clip_id: str, transition_type: str, duration: float) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.set_transition(clip_id, transition_type, duration),
            "clipの切り替えを更新しました",
        )

    @Slot(str, bool, float, float, bool, result=bool)
    def setSequenceClipAudio(
        self,
        clip_id: str,
        audio_linked: bool,
        volume: float,
        audio_offset_seconds: float,
        muted: bool,
    ) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.set_clip_audio(
                clip_id,
                audio_linked=audio_linked,
                volume=volume,
                audio_offset_seconds=audio_offset_seconds,
                muted=muted,
            ),
            "clipの音声設定を更新しました",
        )

    @Slot(int, result=bool)
    def setSequencePlayhead(self, output_milliseconds: int) -> bool:
        backend = self._backend
        model = self._sequence_model_for_facade()
        if model is None or not model.clips:
            return self._sequence_failure("sequenceに再生可能なclipがありません")
        try:
            requested = float(output_milliseconds) / 1000.0
        except (TypeError, ValueError):
            return self._sequence_failure("再生位置が不正です")
        if not math.isfinite(requested):
            return self._sequence_failure("再生位置が不正です")
        backend._sequence_playhead_seconds = min(max(0.0, requested), model.output_duration)
        backend._sequence_error = ""
        backend.sequenceChanged.emit()
        return True
