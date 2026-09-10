param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$LogPath
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-Location $InstallRoot

function Write-ValidationLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    Add-Content -LiteralPath $LogPath -Value ("{0:o} validation: {1}" -f (Get-Date), $Message) -Encoding UTF8
}

$venvPython = Join-Path $InstallRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) { throw "Updated Python runtime is missing." }

$validation = "import json; import PySide6, torch, whisperx; from src.runtime_dependencies import check_runtime_dependencies; s=check_runtime_dependencies(); assert s.ready, s.to_dict(); c=json.load(open(r'.gui/runtime_config.json', encoding='utf-8-sig')); assert c.get('shared',{}).get('device') != 'cuda' or torch.cuda.is_available(), 'configured CUDA is unavailable'"
& $venvPython -c $validation *>> $LogPath
if ($LASTEXITCODE -ne 0) { throw "Updated Python runtime validation failed." }
& $venvPython -m pip check *>> $LogPath
if ($LASTEXITCODE -ne 0) { throw "Updated Python dependency validation failed." }
Write-ValidationLog "Python imports, dependency health, and configured CUDA passed"

$ffmpegRecord = Join-Path $InstallRoot ".local\ffmpeg_path.txt"
if (-not (Test-Path -LiteralPath $ffmpegRecord -PathType Leaf)) { throw "FFmpeg runtime record is missing." }
$ffmpegDirectory = (Get-Content -LiteralPath $ffmpegRecord -Raw -Encoding UTF8).Trim()
foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    $toolPath = Join-Path $ffmpegDirectory $tool
    if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) { throw "$tool is missing from the configured runtime." }
    & $toolPath -version *>> $LogPath
    if ($LASTEXITCODE -ne 0) { throw "$tool runtime validation failed." }
}
Write-ValidationLog "FFmpeg and FFprobe passed"
