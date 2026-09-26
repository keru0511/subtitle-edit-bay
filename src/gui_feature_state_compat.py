"""旧GUIバックエンドから機能別窓口の状態を読むための互換層。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from . import updater


class FeatureStateCompatibility:
    """既存のPython呼び出し・テスト向けに状態属性を保持する。"""

    @property
    def _update_info(self) -> updater.UpdateInfo | None:
        return self._updates_facade._state.info

    @_update_info.setter
    def _update_info(self, value: updater.UpdateInfo | None) -> None:
        self._updates_facade._state.info = value

    @property
    def _update_error(self) -> str:
        return self._updates_facade._state.error

    @_update_error.setter
    def _update_error(self, value: str) -> None:
        self._updates_facade._state.error = value

    @property
    def _update_busy(self) -> bool:
        return self._updates_facade._state.busy

    @_update_busy.setter
    def _update_busy(self, value: bool) -> None:
        self._updates_facade._state.busy = value

    @property
    def _update_package_path(self) -> Path | None:
        return self._updates_facade._state.package_path

    @_update_package_path.setter
    def _update_package_path(self, value: Path | None) -> None:
        self._updates_facade._state.package_path = value

    @property
    def _update_package_sha256(self) -> str:
        return self._updates_facade._state.package_sha256

    @_update_package_sha256.setter
    def _update_package_sha256(self, value: str) -> None:
        self._updates_facade._state.package_sha256 = value

    @property
    def _update_package_ready(self) -> bool:
        return self._updates_facade._state.package_ready

    @_update_package_ready.setter
    def _update_package_ready(self, value: bool) -> None:
        self._updates_facade._state.package_ready = value

    @property
    def _update_download_bytes(self) -> int:
        return self._updates_facade._state.download_bytes

    @_update_download_bytes.setter
    def _update_download_bytes(self, value: int) -> None:
        self._updates_facade._state.download_bytes = value

    @property
    def _update_download_total(self) -> int:
        return self._updates_facade._state.download_total

    @_update_download_total.setter
    def _update_download_total(self, value: int) -> None:
        self._updates_facade._state.download_total = value

    @property
    def _update_download_speed(self) -> float:
        return self._updates_facade._state.download_speed

    @_update_download_speed.setter
    def _update_download_speed(self, value: float) -> None:
        self._updates_facade._state.download_speed = value

    @property
    def _update_download_active(self) -> bool:
        return self._updates_facade._state.download_active

    @_update_download_active.setter
    def _update_download_active(self, value: bool) -> None:
        self._updates_facade._state.download_active = value

    @property
    def _update_download_cancel(self) -> threading.Event:
        return self._updates_facade._state.download_cancel

    @_update_download_cancel.setter
    def _update_download_cancel(self, value: threading.Event) -> None:
        self._updates_facade._state.download_cancel = value

    @property
    def _highlight_candidates(self) -> list[dict[str, Any]]:
        return self._short_video_facade._highlight_state.candidates

    @_highlight_candidates.setter
    def _highlight_candidates(self, value: list[dict[str, Any]]) -> None:
        self._short_video_facade._highlight_state.candidates = value

    @property
    def _highlight_rejected(self) -> list[dict[str, Any]]:
        return self._short_video_facade._highlight_state.rejected

    @_highlight_rejected.setter
    def _highlight_rejected(self, value: list[dict[str, Any]]) -> None:
        self._short_video_facade._highlight_state.rejected = value

    @property
    def _highlight_status(self) -> str:
        return self._short_video_facade._highlight_state.status

    @_highlight_status.setter
    def _highlight_status(self, value: str) -> None:
        self._short_video_facade._highlight_state.status = value

    @property
    def _highlight_progress(self) -> float:
        return self._short_video_facade._highlight_state.progress

    @_highlight_progress.setter
    def _highlight_progress(self, value: float) -> None:
        self._short_video_facade._highlight_state.progress = value

    @property
    def _highlight_cancel(self) -> threading.Event:
        return self._short_video_facade._highlight_state.cancel

    @_highlight_cancel.setter
    def _highlight_cancel(self, value: threading.Event) -> None:
        self._short_video_facade._highlight_state.cancel = value

    @property
    def _highlight_generation(self) -> int:
        return self._short_video_facade._highlight_state.generation

    @_highlight_generation.setter
    def _highlight_generation(self, value: int) -> None:
        self._short_video_facade._highlight_state.generation = value
