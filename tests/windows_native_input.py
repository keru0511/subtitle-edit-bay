from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QPointF, QRect
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest


class _KeyboardInput(ctypes.Structure):
    _fields_ = (
        ("virtual_key", ctypes.c_ushort),
        ("scan_code", ctypes.c_ushort),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("extra_info", ctypes.c_size_t),
    )


class _MouseInput(ctypes.Structure):
    _fields_ = (
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("wheel", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("extra_info", ctypes.c_size_t),
    )


class _HardwareInput(ctypes.Structure):
    _fields_ = (("message", ctypes.c_uint32), ("low", ctypes.c_ushort), ("high", ctypes.c_ushort))


class _InputData(ctypes.Union):
    _fields_ = (("keyboard", _KeyboardInput), ("mouse", _MouseInput), ("hardware", _HardwareInput))


class _Input(ctypes.Structure):
    _fields_ = (("kind", ctypes.c_uint32), ("data", _InputData))


class WindowsNativeInput:
    """Win32の入力ストリームを通し、実際のWindows IMEにキーを処理させる。"""

    KEY_UP = 0x0002
    MOUSE_LEFT_DOWN = 0x0002
    MOUSE_LEFT_UP = 0x0004
    VK_CTRL = 0x11
    VK_CAPITAL = 0x14
    VK_RETURN = 0x0D
    VK_SPACE = 0x20

    def __init__(self, window: QQuickWindow) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windowsの実入力はWindowsでのみ利用できます")
        self.window = window
        self.handle = int(window.winId())
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(_Input), ctypes.c_int)
        self.user32.SendInput.restype = ctypes.c_uint
        self.user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self.user32.SetCursorPos.restype = ctypes.c_int
        self.user32.GetForegroundWindow.restype = ctypes.c_void_p
        self.user32.LoadKeyboardLayoutW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
        self.user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
        self.user32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        self.user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
        self.user32.GetKeyboardLayout.argtypes = (ctypes.c_uint32,)
        self.user32.GetKeyboardLayout.restype = ctypes.c_void_p

    def _send(self, events: list[_Input]) -> None:
        payload = (_Input * len(events))(*events)
        sent = self.user32.SendInput(len(payload), payload, ctypes.sizeof(_Input))
        if sent != len(payload):
            raise OSError(ctypes.get_last_error(), f"Windowsの入力イベントを送信できません: {sent}/{len(payload)}")
        QTest.qWait(50)

    @staticmethod
    def _keyboard(virtual_key: int, *, released: bool = False) -> _Input:
        flags = WindowsNativeInput.KEY_UP if released else 0
        return _Input(1, _InputData(keyboard=_KeyboardInput(virtual_key, 0, flags, 0, 0)))

    @staticmethod
    def _mouse(flags: int) -> _Input:
        return _Input(0, _InputData(mouse=_MouseInput(0, 0, 0, flags, 0, 0)))

    def key(self, virtual_key: int) -> None:
        self._send([self._keyboard(virtual_key), self._keyboard(virtual_key, released=True)])

    def chord(self, modifier: int, virtual_key: int) -> None:
        self._send([
            self._keyboard(modifier),
            self._keyboard(virtual_key),
            self._keyboard(virtual_key, released=True),
            self._keyboard(modifier, released=True),
        ])

    def type_roman(self, value: str) -> None:
        for character in value.upper():
            if not "A" <= character <= "Z":
                raise ValueError(f"ローマ字入力に使えない文字です: {character!r}")
            self.key(ord(character))

    def click(self, item: QQuickItem) -> None:
        top_left = self.window.mapToGlobal(item.mapToScene(QPointF(0, 0)).toPoint())
        bottom_right = self.window.mapToGlobal(item.mapToScene(QPointF(item.width(), item.height())).toPoint())
        item_bounds = QRect(top_left, bottom_right).normalized()
        visible_bounds = item_bounds.intersected(self.window.screen().geometry())
        if visible_bounds.isEmpty():
            raise AssertionError(f"クリック対象が画面外です: {item.objectName()} ({item_bounds})")
        screen_point = visible_bounds.center()
        if not self.user32.SetCursorPos(screen_point.x(), screen_point.y()):
            raise OSError(ctypes.get_last_error(), "Windowsのマウス位置を設定できません")
        self._send([self._mouse(self.MOUSE_LEFT_DOWN), self._mouse(self.MOUSE_LEFT_UP)])

    def activate_japanese_ime(self) -> None:
        if self.user32.GetForegroundWindow() != self.handle:
            raise AssertionError("検証対象のウィンドウが前面にありません")
        layout = self.user32.LoadKeyboardLayoutW("00000411", 1)
        if not layout:
            raise OSError(ctypes.get_last_error(), "日本語入力方式を読み込めません")
        QTest.qWait(200)
        thread_id = self.user32.GetWindowThreadProcessId(self.handle, None)
        active_layout = self.user32.GetKeyboardLayout(thread_id)
        if not active_layout or active_layout & 0xFFFF != 0x0411:
            raise AssertionError(f"日本語入力方式に切り替わっていません: {active_layout!r}")
        # Alt+` はトグルなので連続テストで逆方向に切り替わる。
        # Ctrl+CapsLock はMicrosoft日本語IMEをひらがなモードへ設定する。
        self.chord(self.VK_CTRL, self.VK_CAPITAL)
