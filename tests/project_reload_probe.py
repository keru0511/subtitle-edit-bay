from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine

from src.data_boundary import is_object_list
from src.gui import EditBayBackend


class _ProbeArgs(argparse.Namespace):
    project: str = ""
    result: str = ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(namespace=_ProbeArgs())

    repository_root = Path(__file__).resolve().parents[1]
    project_path = Path(args.project).resolve()
    # 再読込の検証を外部AIプロセスの接続・終了待ちに依存させない。
    with patch("src.gui.CodexChatController.connect"):
        backend = EditBayBackend([], workspace_root=project_path.parent)
    engine = QQmlApplicationEngine()
    try:
        backend.loadProject(str(project_path))
        engine.rootContext().setContextProperty("backend", backend)
        engine.load(QUrl.fromLocalFile(str(repository_root / "src" / "ui" / "Main.qml")))
        backend.processEvents()

        root_objects: object = engine.rootObjects()
        if not is_object_list(root_objects):
            raise RuntimeError("QMLのルート要素を取得できません")
        root = root_objects[0] if root_objects else None
        if root is not None and not isinstance(root, QObject):
            raise RuntimeError("QMLのルート要素がQObjectではありません")
        edit_button = root.findChild(QObject, "editSubtitlesButton") if root else None
        segments: object = backend.subtitleSegments
        settings: object = backend.settings
        edit_button_enabled: object = False
        if edit_button is not None:
            edit_button_enabled = edit_button.property("enabled")
        result = {
            "project_loaded": backend.projectLoaded,
            "project_path": backend.projectPath,
            "project_dirty": backend.projectDirty,
            "segments": segments,
            "settings": settings,
            "qml_loaded": root is not None,
            "edit_button_enabled": bool(edit_button_enabled),
        }
        Path(args.result).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    finally:
        backend._project_dirty = False
        engine.clearComponentCache()
        backend._shutdown_executor()


if __name__ == "__main__":
    main()
