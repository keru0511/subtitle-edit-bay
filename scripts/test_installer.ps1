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
    "SubtitleEditBayLauncher.exe",
    "src\gui.py",
    "src\ui\Main.qml",
    "scripts\launch.ps1",
    "setup.bat",
    "start.bat",
    "update.bat",
    "requirements.txt",
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

$venvPython = Join-Path $installDir ".venv\Scripts\python.exe"
python -m venv (Join-Path $installDir ".venv")
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $installDir "requirements.txt")

$launcher = Join-Path $installDir "SubtitleEditBayLauncher.exe"
$partialProbe = Start-Process -FilePath $launcher -ArgumentList "--probe-setup" -WorkingDirectory $installDir -Wait -PassThru
if ($partialProbe.ExitCode -ne 3) {
    throw "A partial environment was accepted before setup completion: exit=$($partialProbe.ExitCode)"
}

$statusDirectory = Join-Path $installDir ".local"
New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
@{
    schema_version = 1
    status = "success"
    app_version = $expectedInstalledVersion
    message = "CI dependency provider completed"
    details = @{ provider = "ci-cpu" }
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $statusDirectory "setup-status.json") -Encoding UTF8

$readyProbe = Start-Process -FilePath $launcher -ArgumentList "--probe-setup" -WorkingDirectory $installDir -Wait -PassThru
if ($readyProbe.ExitCode -ne 0) {
    throw "A completed setup environment was rejected: exit=$($readyProbe.ExitCode)"
}

$smokeResult = Join-Path ([IO.Path]::GetDirectoryName($installDir)) "installed-gui-smoke.json"
$env:SUBTITLE_EDIT_BAY_STARTUP_SMOKE_RESULT = $smokeResult
try {
    $launch = Start-Process -FilePath $launcher -WorkingDirectory $installDir -Wait -PassThru
    if ($launch.ExitCode -ne 0) { throw "Product launcher exited with code $($launch.ExitCode)." }
} finally {
    Remove-Item Env:SUBTITLE_EDIT_BAY_STARTUP_SMOKE_RESULT -ErrorAction SilentlyContinue
}
if (-not (Test-Path -LiteralPath $smokeResult -PathType Leaf)) {
    throw "Product launcher did not record GUI readiness: $smokeResult"
}
$smoke = Get-Content -LiteralPath $smokeResult -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $smoke.qmlLoaded -or $smoke.entrypoint -ne "SubtitleEditBayLauncher.exe") {
    throw "Installed GUI readiness contract failed: $($smoke | ConvertTo-Json -Compress)"
}
if ($smoke.version -ne $ExpectedVersion -or $smoke.distribution -ne "installer") {
    throw "Installed GUI identity mismatch: $($smoke | ConvertTo-Json -Compress)"
}
