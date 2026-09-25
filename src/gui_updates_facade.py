from __future__ import annotations

from typing import TYPE_CHECKING

import subprocess
import sys
import threading
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


class UpdateFacade(FeatureFacade):
    """アプリケーション更新の画面窓口。"""

    updateBusyChanged = Signal()
    updateDownloadProgressChanged = Signal()
    updateErrorChanged = Signal()
    updateInfoChanged = Signal()
    updatePackageReadyChanged = Signal()

    def __init__(self, backend: "EditBayBackend") -> None:
        super().__init__(backend)
        backend.updateBusyChanged.connect(self.updateBusyChanged.emit)
        backend.updateDownloadProgressChanged.connect(self.updateDownloadProgressChanged.emit)
        backend.updateErrorChanged.connect(self.updateErrorChanged.emit)
        backend.updateInfoChanged.connect(self.updateInfoChanged.emit)
        backend.updatePackageReadyChanged.connect(self.updatePackageReadyChanged.emit)

    @Property(str, notify=updateInfoChanged)
    def updateCurrentVersion(self) -> str:
        backend = self._backend
        return backend._update_info.current_version if backend._update_info else ""

    @Property(str, notify=updateInfoChanged)
    def updateLatestVersion(self) -> str:
        backend = self._backend
        return backend._update_info.latest_version if backend._update_info else ""

    @Property(str, notify=updateInfoChanged)
    def updateReleaseNotes(self) -> str:
        backend = self._backend
        return backend._update_info.release_notes if backend._update_info else ""

    @Property(str, notify=updateInfoChanged)
    def updateDownloadUrl(self) -> str:
        backend = self._backend
        return backend._update_info.download_url if backend._update_info else ""

    @Property(bool, notify=updateInfoChanged)
    def updateAvailable(self) -> bool:
        backend = self._backend
        return backend._update_info.available if backend._update_info else False

    @Property(bool, notify=updateBusyChanged)
    def updateBusy(self) -> bool:
        backend = self._backend
        return backend._update_busy

    @Property(str, notify=updateErrorChanged)
    def updateError(self) -> str:
        backend = self._backend
        return backend._update_error

    @Property(int, notify=updateDownloadProgressChanged)
    def updateDownloadBytes(self) -> int:
        backend = self._backend
        return backend._update_download_bytes

    @Property(int, notify=updateDownloadProgressChanged)
    def updateDownloadTotal(self) -> int:
        backend = self._backend
        return backend._update_download_total

    @Property(float, notify=updateDownloadProgressChanged)
    def updateDownloadSpeed(self) -> float:
        backend = self._backend
        return backend._update_download_speed

    @Property(bool, notify=updateDownloadProgressChanged)
    def updateDownloadActive(self) -> bool:
        backend = self._backend
        return backend._update_download_active

    @Property(bool, notify=updatePackageReadyChanged)
    def updatePackageReady(self) -> bool:
        backend = self._backend
        return backend._update_package_ready

    @Property(int, notify=updateInfoChanged)
    def updatePackageSize(self) -> int:
        backend = self._backend
        return int(backend._update_info.package_size) if backend._update_info else 0

    @Slot()
    def checkForUpdates(self) -> None:
        backend = self._backend
        if backend._update_busy:
            return
        backend._update_busy = True
        backend.updateBusyChanged.emit()
        backend._update_error = ""
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
        backend._update_info = info
        backend._update_error = error
        backend._update_busy = False
        backend._update_package_path = None
        backend._update_package_sha256 = ""
        backend._update_package_ready = False
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
        if backend._update_download_active:
            backend._set_status("ダウンロード中は更新画面を閉じられません", "UPDATE")
            return
        backend._update_info = None
        backend._update_error = ""
        backend._update_package_path = None
        backend._update_package_sha256 = ""
        backend._update_package_ready = False
        backend.updateInfoChanged.emit()
        backend.updateErrorChanged.emit()
        backend.updatePackageReadyChanged.emit()

    @Slot()
    def downloadUpdate(self) -> None:
        backend = self._backend
        if backend._update_busy or backend._update_download_active:
            return
        if not backend._update_info or not backend._update_info.available:
            backend._set_status("更新可能なバージョンがありません", "CHECK")
            return
        if getattr(backend._update_info, "package_type", "archive") != "installer":
            backend._set_status("この配布形態は従来の更新方法を使用します", "UPDATE")
            return
        backend._update_busy = True
        backend._update_download_active = True
        backend._update_download_cancel = threading.Event()
        backend._update_download_bytes = 0
        backend._update_download_total = int(backend._update_info.package_size or 0)
        backend._update_download_speed = 0.0
        backend._update_error = ""
        backend.updateBusyChanged.emit()
        backend.updateErrorChanged.emit()
        backend.updateDownloadProgressChanged.emit()
        backend._set_status("更新パッケージをダウンロードしています", "UPDATE")
        threading.Thread(target=self._download_update_worker, daemon=True).start()

    def _download_update_worker(self) -> None:
        backend = self._backend
        info = backend._update_info
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
                cancel_event=backend._update_download_cancel,
                progress_callback=lambda downloaded, total, speed: backend.updateDownloadProgressEvent.emit(
                    downloaded, total, speed
                ),
            )
            backend._update_package_sha256 = expected_sha256
            backend.updateDownloadFinished.emit(str(package_path), "")
        except update_manager.UpdateDownloadCancelled as error:
            backend.updateDownloadFinished.emit("", str(error))
        except update_manager.UpdatePackageError as error:
            backend.updateDownloadFinished.emit("", str(error))
        except Exception as error:
            backend.updateDownloadFinished.emit("", f"更新パッケージの取得に失敗しました: {error}")

    def _on_update_download_progress(self, downloaded: int, total: int, speed: float) -> None:
        backend = self._backend
        backend._update_download_bytes = downloaded
        backend._update_download_total = total
        backend._update_download_speed = speed
        backend.updateDownloadProgressChanged.emit()

    def _on_update_download_finished(self, package_path: str, error: str) -> None:
        backend = self._backend
        backend._update_download_active = False
        backend._update_busy = False
        if error:
            backend._update_error = error
            backend._update_package_path = None
            backend._update_package_ready = False
            backend._set_status(error, "ERROR" if "キャンセル" not in error else "CANCELLED")
        else:
            backend._update_error = ""
            backend._update_package_path = Path(package_path)
            backend._update_package_ready = True
            backend._set_status("更新パッケージを検証しました。再起動して更新できます", "READY")
        backend.updateBusyChanged.emit()
        backend.updateErrorChanged.emit()
        backend.updateDownloadProgressChanged.emit()
        backend.updatePackageReadyChanged.emit()

    @Slot()
    def cancelUpdateDownload(self) -> None:
        backend = self._backend
        if backend._update_download_active:
            backend._update_download_cancel.set()
            backend._set_status("更新パッケージのダウンロードをキャンセルしています", "UPDATE")

    @Slot()
    def applyDownloadedUpdate(self) -> None:
        backend = self._backend
        if not backend._update_package_ready or not backend._update_package_path or not backend._update_info:
            backend._set_status("検証済みの更新パッケージがありません", "CHECK")
            return
        if backend._running:
            backend._set_status("処理中は更新を開始できません", "BUSY")
            return
        if backend._project_dirty:
            backend._set_status("未保存の変更があります。更新前に保存してください", "CHECK")
            return
        if backend._project is not None and not backend.saveProject():
            backend._set_status("プロジェクトを保存できませんでした", "ERROR")
            return
        backend.saveSettings(backend._settings)
        try:
            expected_sha256 = backend._update_package_sha256 or update_manager.resolve_expected_sha256(
                backend._update_info
            )
            result_path = update_manager.update_download_directory(backend.workspace_root) / "last-update-result.json"
            command = update_manager.build_installer_helper_command(
                backend.workspace_root,
                backend._update_package_path,
                expected_version=backend._update_info.latest_version,
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
        if backend._project_dirty:
            backend._set_status("未保存の変更があります。更新前に保存してください", "CHECK")
            return
        if not backend._update_info or not backend._update_info.available:
            backend._set_status("更新可能なバージョンがありません", "CHECK")
            return
        if getattr(backend._update_info, "package_type", "archive") == "installer":
            if backend._update_package_ready:
                self.applyDownloadedUpdate()
            else:
                self.downloadUpdate()
            return
        try:
            command = updater.launch_update_script(backend.workspace_root, backend._update_info.download_url)
        except updater.UpdaterError as error:
            backend._set_status(str(error), "ERROR")
            return
        if backend._project is not None and not backend.saveProject():
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
