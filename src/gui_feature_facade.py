from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from .gui import EditBayBackend


class FeatureFacade(QObject):
    """機能別のQML窓口が共有するライフサイクル。

    アプリケーションを親にしてQMLとの寿命を揃える。データの所有者は
    既存のコントローラーのままとし、状態を複製する汎用転送は行わない。
    """

    def __init__(self, backend: EditBayBackend) -> None:
        super().__init__(backend)
        self._backend = backend
