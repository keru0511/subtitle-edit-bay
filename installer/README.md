# Windows installer

`SubtitleEditBay.iss` packages the source distribution as a per-user Windows
installer. It deliberately does not package `.venv`, `.gui`, `.local`, imported
videos, exports, or generated output.

Build from the repository root with Inno Setup 6 installed:

```powershell
pwsh -File scripts/build_installer.ps1 `
  -Version 1.0.0 `
  -OutputPath dist/SubtitleEditBay-Setup.exe
```

The build script locates `ISCC.exe` from `PATH`, the standard Inno Setup install
directory, or the `INNO_SETUP_COMPILER` environment variable. It does not install
or download build dependencies.

The installer build also attempts to compile `launcher/SubtitleEditBayLauncher.c`
with Visual C++ `cl.exe`. The native launcher resolves its own installation
directory before invoking `scripts/launch.ps1`. If `cl.exe` is unavailable, the
build remains compatible and the installed shortcut falls back to Windows
PowerShell. To build the native launcher explicitly, run:

```powershell
pwsh -File scripts/build_launcher.ps1
```

The launcher never uses the current working directory to find the application,
so shortcuts and file associations remain valid when started from another
directory. It does not elevate privileges.

Upgrading with the same AppId overwrites application files while retaining the
virtual environment and user-generated data. Uninstalling removes the generated
virtual environment but retains GUI settings, custom speaker colours, imported
videos, exports, output, and update backups.
