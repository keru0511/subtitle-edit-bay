from __future__ import annotations

from typing import TYPE_CHECKING

import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Property,
    Signal,
    Slot,
)

from .platform_updates import installer_download_name
from .process_utils import detached_subprocess_kwargs

from . import update_manager, updater

from .gui_feature_facade import FeatureFacade

if TYPE_CHECKING:
    from .gui import EditBayBackend


@dataclass(slots=True)
class UpdateState:
    """更新確認とダウンロードの一時状態。"""

    info: updater.UpdateInfo | None = None
    error: str = ""
    busy: bool = False
    package_path: Path | None = None
    package_sha256: str = ""
    package_ready: bool = False
    download_bytes: int = 0
    download_total: int = 0
    download_speed: float = 0.0
    download_active: bool = False
    download_cancel: threading.Event = field(default_factory=threading.Event)


class UpdateFacade(FeatureFacade):
    """アプリケーション更新の画面窓口。"""

    updateBusyChanged = Signal()
    updateDownloadProgressChanged = Signal()
    updateErrorChanged = Signal()
    updateInfoChanged = Signal()
    updatePackageReadyChanged = Signal()

    def __init__(self, backend: "EditBayBackend") -> None:
        super().__init__(backend)
        self._state = UpdateState()
        backend.updateBusyChanged.connect(self.updateBusyChanged.emit)
        backend.updateDownloadProgressChanged.connect(self.updateDownloadProgressChanged.emit)
        backend.updateErrorChanged.connect(self.updateErrorChanged.emit)
        backend.updateInfoChanged.connect(self.updateInfoChanged.emit)
        backend.updatePackageReadyChanged.connect(self.updatePackageReadyChanged.emit)

    @Property(str, notify=updateInfoChanged)
    def updateCurrentVersion(self) -> str:
        return self._state.info.current_version if self._state.info else ""

    @Property(str, notify=updateInfoChanged)
    def updateLatestVersion(self) -> str:
        return self._state.info.latest_version if self._state.info else ""

    @Property(str, notify=updateInfoChanged)
    def updateReleaseNotes(self) -> str:
        return self._state.info.release_notes if self._state.info else ""

    @Property(str, notify=updateInfoChanged)
    def updateDownloadUrl(self) -> str:
        return self._state.info.download_url if self._state.info else ""

    @Property(bool, notify=updateInfoChanged)
    def updateAvailable(self) -> bool:
        return self._state.info.available if self._state.info else False

    @Property(bool, notify=updateBusyChanged)
    def updateBusy(self) -> bool:
        return self._state.busy

    @Property(str, notify=updateErrorChanged)
    def updateError(self) -> str:
        return self._state.error

    @Property(int, notify=updateDownloadProgressChanged)
    def updateDownloadBytes(self) -> int:
        return self._state.download_bytes

    @Property(int, notify=updateDownloadProgressChanged)
    def updateDownloadTotal(self) -> int:
        return self._state.download_total

    @Property(float, notify=updateDownloadProgressChanged)
    def updateDownloadSpeed(self) -> float:
        return self._state.download_speed

    @Property(bool, notify=updateDownloadProgressChanged)
    def updateDownloadActive(self) -> bool:
        return self._state.download_active

    @Property(bool, notify=updatePackageReadyChanged)
    def updatePackageReady(self) -> bool:
        return self._state.package_ready

    @Property(int, notify=updateInfoChanged)
    def updatePackageSize(self) -> int:
        return int(self._state.info.package_size) if self._state.info else 0

    @Slot()
    def checkForUpdates(self) -> None:
        backend = self._backend
        if self._state.busy:
            return
        self._state.busy = True
        backend.updateBusyChanged.emit()
        self._state.error = ""
        backend.updateErrorChanged.emit()
        backend._set_status("最新リリースを確認しています", "UPDATE")
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

    def _check_for_updates_worker(self) -> None:
        backend = self._backend
        try:
            info = updater.fetch_latest_release(backend.workspace_root)
            backend.updateCheckFinished.emit(info, "")
        except updater.UpdaterError as error:
            backend.updateCheckFinished.emit(None, str(error))
        except Exception as error:
            backend.updateCheckFinished.emit(None, f"更新確認に失敗しました: {error}")

    def _on_update_check_finished(self, info: Any, error: str) -> None:
        backend = self._backend
        self._state.info = info
        self._state.error = error
        self._state.busy = False
        self._state.package_path = None
        self._state.package_sha256 = ""
        self._state.package_ready = False
        backend.updateInfoChanged.emit()
        backend.updateErrorChanged.emit()
        backend.updatePackageReadyChanged.emit()
        backend.updateBusyChanged.emit()
        if error:
            backend._set_status(error, "ERROR")
        elif info and info.available:
            backend._set_status(f"新しいバージョン {info.latest_version} が利用可能です", "READY")
        elif info:
            backend._set_status(f"最新バージョンです ({info.current_version})", "READY")

    @Slot()
    def dismissUpdateInfo(self) -> None:
        backend = self._backend
        if backend._running and backend._active_job == "update":
            backend._set_status("更新中は更新画面を閉じられません", "UPDATE")
            return
        if self._state.download_active:
            backend._set_status("ダウンロード中は更新画面を閉じられません", "UPDATE")
            return
        self._state.info = None
        self._state.error = ""
        self._state.package_path = None
        self._state.package_sha256 = ""
        self._state.package_ready = False
        backend.updateInfoChanged.emit()
        backend.updateErrorChanged.emit()
        backend.updatePackageReadyChanged.emit()

    @Slot()
    def downloadUpdate(self) -> None:
        backend = self._backend
        if self._state.busy or self._state.download_active:
            return
        if not self._state.info or not self._state.info.available:
            backend._set_status("更新可能なバージョンがありません", "CHECK")
            return
        if getattr(self._state.info, "package_type", "archive") != "installer":
            backend._set_status("この配布形態は従来の更新方法を使用します", "UPDATE")
            return
        self._state.busy = True
        self._state.download_active = True
        self._state.download_cancel = threading.Event()
        self._state.download_bytes = 0
        self._state.download_total = int(self._state.info.package_size or 0)
        self._state.download_speed = 0.0
        self._state.error = ""
        backend.updateBusyChanged.emit()
        backend.updateErrorChanged.emit()
        backend.updateDownloadProgressChanged.emit()
        backend._set_status("更新パッケージをダウンロードしています", "UPDATE")
        threading.Thread(target=self._download_update_worker, daemon=True).start()

    def _download_update_worker(self) -> None:
        backend = self._backend
        info = self._state.info
        if info is None:
            backend.updateDownloadFinished.emit("", "更新情報がありません")
            return
        try:
            expected_sha256 = update_manager.resolve_expected_sha256(info)
            destination = update_manager.update_download_directory(backend.workspace_root) / (
                installer_download_name(info.latest_version)
            )
            package_path = update_manager.download_package(
                info,
                destination,
                cancel_event=self._state.download_cancel,
                progress_callback=lambda downloaded, total, speed: backend.updateDownloadProgressEvent.emit(
                    downloaded, total, speed
                ),
            )
            self._state.package_sha256 = expected_sha256
            backend.updateDownloadFinished.emit(str(package_path), "")
        except update_manager.UpdateDownloadCancelled as error:
            backend.updateDownloadFinished.emit("", str(error))
        except update_manager.UpdatePackageError as error:
            backend.updateDownloadFinished.emit("", str(error))
        except Exception as error:
            backend.updateDownloadFinished.emit("", f"更新パッケージの取得に失敗しました: {error}")

    def _on_update_download_progress(self, downloaded: int, total: int, speed: float) -> None:
        backend = self._backend
        self._state.download_bytes = downloaded
        self._state.download_total = total
        self._state.download_speed = speed
        backend.updateDownloadProgressChanged.emit()

    def _on_update_download_finished(self, package_path: str, error: str) -> None:
        backend = self._backend
        self._state.download_active = False
        self._state.busy = False
        if error:
            self._state.error = error
            self._state.package_path = None
            self._state.package_ready = False
            backend._set_status(error, "ERROR" if "キャンセル" not in error else "CANCELLED")
        else:
            self._state.error = ""
            self._state.package_path = Path(package_path)
            self._state.package_ready = True
            backend._set_status("更新パッケージを検証しました。再起動して更新できます", "READY")
        backend.updateBusyChanged.emit()
        backend.updateErrorChanged.emit()
        backend.updateDownloadProgressChanged.emit()
        backend.updatePackageReadyChanged.emit()

    @Slot()
    def cancelUpdateDownload(self) -> None:
        backend = self._backend
        if self._state.download_active:
            self._state.download_cancel.set()
            backend._set_status("更新パッケージのダウンロードをキャンセルしています", "UPDATE")

    @Slot()
    def applyDownloadedUpdate(self) -> None:
        backend = self._backend
        if not self._state.package_ready or not self._state.package_path or not self._state.info:
            backend._set_status("検証済みの更新パッケージがありません", "CHECK")
            return
        if backend._running:
            backend._set_status("処理中は更新を開始できません", "BUSY")
            return
        if self.project_editor.project_dirty:
            backend._set_status("未保存の変更があります。更新前に保存してください", "CHECK")
            return
        if self.project_editor.project is not None and not backend.saveProject():
            backend._set_status("プロジェクトを保存できませんでした", "ERROR")
            return
        backend.saveSettings(backend._settings)
        try:
            expected_sha256 = self._state.package_sha256 or update_manager.resolve_expected_sha256(self._state.info)
            result_path = update_manager.update_download_directory(backend.workspace_root) / "last-update-result.json"
            command = update_manager.build_installer_helper_command(
                backend.workspace_root,
                self._state.package_path,
                expected_version=self._state.info.latest_version,
                expected_sha256=expected_sha256,
                result_path=result_path,
            )
            popen_kwargs: dict[str, Any] = {
                "cwd": str(backend.workspace_root),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                **detached_subprocess_kwargs(),
            }
            subprocess.Popen(command, **popen_kwargs)
        except (OSError, update_manager.UpdatePackageError) as error:
            backend._set_status(f"更新helperを起動できませんでした: {error}", "ERROR")
            return
        backend._set_status("GUIを終了して更新を適用します", "UPDATE")
        backend.quit()

    @Slot()
    def applyUpdate(self) -> None:
        backend = self._backend
        if backend._running:
            backend._set_status("処理中は更新を開始できません", "BUSY")
            return
        if self.project_editor.project_dirty:
            backend._set_status("未保存の変更があります。更新前に保存してください", "CHECK")
            return
        if not self._state.info or not self._state.info.available:
            backend._set_status("更新可能なバージョンがありません", "CHECK")
            return
        if getattr(self._state.info, "package_type", "archive") == "installer":
            if self._state.package_ready:
                self.applyDownloadedUpdate()
            else:
                self.downloadUpdate()
            return
        try:
            command = updater.launch_update_script(backend.workspace_root, self._state.info.download_url)
        except updater.UpdaterError as error:
            backend._set_status(str(error), "ERROR")
            return
        if self.project_editor.project is not None and not backend.saveProject():
            backend._set_status("プロジェクトを保存できませんでした", "ERROR")
            return
        backend.saveSettings(backend._settings)
        backend.workflow._start_command(command, "update", "アプリケーションを更新しています")

    @Slot()
    def restartApplication(self) -> None:
        backend = self._backend
        try:
            subprocess.Popen(
                [sys.executable, "-m", "src.gui"],
                cwd=str(backend.workspace_root),
                **detached_subprocess_kwargs(),
            )
        except OSError as error:
            backend._set_status(f"再起動に失敗しました: {error}", "ERROR")
            return
        backend.quit()
