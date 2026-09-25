"""OSに依存する保存先の境界。既存のWindows保存先を維持する。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def log_directory(workspace_root: str | Path) -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Subtitle Edit Bay" / "logs"
    return Path(workspace_root) / ".local" / "logs"


def update_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or tempfile.gettempdir()
    return Path(base) / "SubtitleEditBay" / "updates"


def audio_preview_directory() -> Path:
    # Qtの依存はGUIがこの保存先を要求した場合だけ読み込む。
    from PySide6.QtCore import QStandardPaths

    base = os.environ.get("LOCALAPPDATA") or QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericCacheLocation
    )
    return Path(base) / "Subtitle Edit Bay" / "audio-preview"
