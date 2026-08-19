from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine

from src.gui import EditBayBackend


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parents[1]
    project_path = Path(args.project).resolve()
    backend = EditBayBackend([], workspace_root=repository_root)
    engine = QQmlApplicationEngine()
    try:
        backend.loadProject(str(project_path))
        engine.rootContext().setContextProperty("backend", backend)
        engine.load(QUrl.fromLocalFile(str(repository_root / "src" / "ui" / "Main.qml")))
        backend.processEvents()

        root = engine.rootObjects()[0] if engine.rootObjects() else None
        edit_button = root.findChild(QObject, "editSubtitlesButton") if root else None
        result = {
            "project_loaded": backend.projectLoaded,
            "project_path": backend.projectPath,
            "project_dirty": backend.projectDirty,
            "segments": backend.subtitleSegments,
            "settings": backend.settings,
            "qml_loaded": root is not None,
            "edit_button_enabled": bool(edit_button and edit_button.property("enabled")),
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
