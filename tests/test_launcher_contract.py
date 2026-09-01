from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_native_launcher_resolves_module_directory_and_hides_console() -> None:
    source = (ROOT / "launcher" / "SubtitleEditBayLauncher.c").read_text(encoding="utf-8")
    assert "GetModuleFileNameW" in source
    assert "GetCurrentDirectory" not in source
    assert r"scripts\\launch.ps1" in source
    assert "CreateProcessW" in source
    assert "CREATE_NO_WINDOW" in source
    assert "STARTF_USESHOWWINDOW" in source
    assert "SW_HIDE" in source
    assert "/SUBSYSTEM:WINDOWS" in (ROOT / "scripts" / "build_launcher.ps1").read_text(encoding="utf-8")


def test_installer_has_native_launcher_and_power_shell_fallback() -> None:
    installer = (ROOT / "installer" / "SubtitleEditBay.iss").read_text(encoding="utf-8")
    assert "SubtitleEditBayLauncher.exe" in installer
    assert "skipifsourcedoesntexist" in installer
    assert "WindowsPowerShell" in installer
