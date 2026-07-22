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

Upgrading with the same AppId overwrites application files while retaining the
virtual environment and user-generated data. Uninstalling removes the generated
virtual environment but retains GUI settings, custom speaker colours, imported
videos, exports, output, and update backups.
