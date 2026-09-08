param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$')]
    [string]$ExpectedVersion,

    [Parameter(Mandatory = $true)]
    [string]$InstallDirectory
)

$ErrorActionPreference = "Stop"
$installer = [IO.Path]::GetFullPath($InstallerPath)
$installDir = [IO.Path]::GetFullPath($InstallDirectory)
$logPath = Join-Path ([IO.Path]::GetDirectoryName($installDir)) "subtitle-edit-bay-install.log"
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "Installer is missing: $installer"
}

$arguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/DIR=$installDir",
    "/LOG=$logPath"
)
$process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
if ($process.ExitCode -ne 0) {
    Get-Content -LiteralPath $logPath -ErrorAction SilentlyContinue
    throw "Installer exited with code $($process.ExitCode)."
}
foreach ($path in @(
    "src\gui.py",
    "src\ui\Main.qml",
    "scripts\launch.ps1",
    "setup.bat",
    "start.bat",
    "update.bat",
    "requirements.txt",
    "scripts\runtime_activation.ps1",
    "scripts\runtime_contract.py",
    "runtime\runtime-contract.json",
    "runtime\requirements-windows-cpu.lock",
    "runtime\requirements-windows-cu128.lock",
    "VERSION"
)) {
    $candidate = Join-Path $installDir $path
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw "Installed file is missing: $candidate"
    }
}
$installedVersion = (Get-Content -LiteralPath (Join-Path $installDir "VERSION") -Raw).Trim()
$expectedInstalledVersion = $ExpectedVersion.Substring(1)
if ($installedVersion -ne $expectedInstalledVersion) {
    throw "Installed VERSION mismatch: expected=$expectedInstalledVersion actual=$installedVersion"
}

$setupScript = Join-Path $installDir "scripts\setup.ps1"
Push-Location $installDir
try {
    & $setupScript
    if ($LASTEXITCODE -ne 0) { throw "Installed setup exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}

$runtimeManifestPath = Join-Path $installDir ".local\runtime-manifest.json"
if (-not (Test-Path -LiteralPath $runtimeManifestPath -PathType Leaf)) {
    throw "Installed setup did not publish an active runtime manifest."
}
$runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $runtimeManifest.runtime_directory) {
    throw "Installed runtime manifest has no runtime_directory."
}
$runtimeDirectory = [IO.Path]::GetFullPath((Join-Path $installDir ([string]$runtimeManifest.runtime_directory)))
$venvPython = Join-Path $runtimeDirectory "Scripts\python.exe"
$venvPip = Join-Path $runtimeDirectory "Scripts\pip.exe"
foreach ($runtimeCommand in @($venvPython, $venvPip)) {
    if (-not (Test-Path -LiteralPath $runtimeCommand -PathType Leaf)) {
        throw "Installed runtime command is missing: $runtimeCommand"
    }
}
& $venvPip --version
if ($LASTEXITCODE -ne 0) { throw "Installed runtime pip command is not relocatable-safe." }

$smokeScript = Join-Path ([IO.Path]::GetDirectoryName($installDir)) "installed-gui-smoke.py"
@'
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine

from src.gui import APP_TITLE, EditBayBackend

app = EditBayBackend(["subtitle-edit-bay-installed-smoke"], workspace_root=Path.cwd())
app.setApplicationName(APP_TITLE)
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("backend", app)
qml_path = Path("src/ui/Main.qml").resolve()
engine.load(QUrl.fromLocalFile(str(qml_path)))
if not engine.rootObjects():
    raise SystemExit(f"Could not load installed GUI: {qml_path}")
QTimer.singleShot(500, app.quit)
exit_code = app.exec()
if exit_code != 0:
    raise SystemExit(exit_code)
'@ | Set-Content -LiteralPath $smokeScript -Encoding UTF8
Push-Location $installDir
try {
    & $venvPython $smokeScript
    if ($LASTEXITCODE -ne 0) { throw "Installed GUI smoke exited with code $LASTEXITCODE." }
}
finally {
    Pop-Location
}
