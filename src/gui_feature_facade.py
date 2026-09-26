from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from .gui import EditBayBackend
    from .gui_project_editor_controller import ProjectEditorController


class FeatureFacade(QObject):
    """機能別のQML窓口が共有するライフサイクル。

    アプリケーションを親にしてQMLとの寿命を揃える。データの所有者は
    既存のコントローラーのままとし、状態を複製する汎用転送は行わない。
    """

    def __init__(self, backend: EditBayBackend) -> None:
        super().__init__(backend)
        self._backend = backend
        self._project_editor: ProjectEditorController | None = None

    def bind_project_editor(self, controller: ProjectEditorController) -> None:
        """プロジェクトの所有者を機能窓口へ直接渡す。"""

        self._project_editor = controller

    @property
    def project_editor(self) -> ProjectEditorController:
        controller = self._project_editor
        if controller is None:
            raise RuntimeError("プロジェクト編集器の初期化が完了していません")
        return controller
