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

The installer build compiles `launcher/SubtitleEditBayLauncher.c` with the static
MSVC runtime and embedded product/version resources. `cl.exe` and `rc.exe` are
required; a missing native compiler is a build failure. Release binaries must
additionally satisfy `docs/WINDOWS_BINARY_TRUST.md`; an unsigned formal release is
not an allowed fallback. To build the native launcher explicitly, run:

```powershell
pwsh -File scripts/build_launcher.ps1 -Version 1.0.0
```

The launcher never uses the current working directory to find the application,
so shortcuts and file associations remain valid when started from another
directory. It does not elevate privileges.

Upgrading with the same AppId overwrites application files while retaining the
virtual environment and user-generated data. Uninstalling removes the generated
virtual environment but retains GUI settings, custom speaker colours, imported
videos, exports, output, and update backups.
