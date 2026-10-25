"""Qtスタブに未収録の音声バッファ出力を実行時APIに沿って型付けする。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from PySide6.QtCore import QObject
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat


@runtime_checkable
class AudioBufferSignal(Protocol):
    def connect(self, callback: Callable[[QAudioBuffer], None]) -> object: ...


if TYPE_CHECKING:
    class QAudioBufferOutput(QObject):
        audioBufferReceived: AudioBufferSignal

        def __init__(self, audio_format: QAudioFormat, parent: QObject | None = None) -> None: ...
else:
    from PySide6.QtMultimedia import QAudioBufferOutput as QAudioBufferOutput
