param(
    [ValidateSet("Launch", "Setup", "Update")]
    [string]$Action = "Launch",
    [switch]$ProbeSetupStateOnly,
    [switch]$ProbeCudaRepairOnly,
    [switch]$SuppressMessages,
    [string]$ProjectRootOverride = "",
    [string]$PythonOverride = "",
    [string]$PythonwOverride = "",
    [string]$SetupExecutableOverride = "",
    [string]$LogDirectoryOverride = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = if ($ProjectRootOverride) {
    [IO.Path]::GetFullPath($ProjectRootOverride)
} else {
    [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
}
$pythonw = if ($PythonwOverride) {
    [IO.Path]::GetFullPath($PythonwOverride)
} else {
    Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
}
$python = if ($PythonOverride) {
    [IO.Path]::GetFullPath($PythonOverride)
} else {
    Join-Path $projectRoot ".venv\Scripts\python.exe"
}
$setupExecutable = if ($SetupExecutableOverride) {
    [IO.Path]::GetFullPath($SetupExecutableOverride)
} else {
    Join-Path $projectRoot "setup.bat"
}
$updateExecutable = Join-Path $projectRoot "update.bat"

function Test-SetupComplete {
    $statusPath = Join-Path $projectRoot ".local\setup-status.json"
    $versionPath = Join-Path $projectRoot "VERSION"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
        -not (Test-Path -LiteralPath $pythonw -PathType Leaf) -or
        -not (Test-Path -LiteralPath $statusPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $versionPath -PathType Leaf)) { return $false }
    try {
        $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $version = (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8).Trim()
        return [string]$status.status -eq "success" -and [string]$status.app_version -eq $version
    } catch { return $false }
}

if ($ProbeSetupStateOnly) {
    if (Test-SetupComplete) { exit 0 }
    exit 3
}
if ($Action -eq "Setup") {
    $process = Start-Process -FilePath $setupExecutable -WorkingDirectory $projectRoot -Wait -PassThru
    exit $process.ExitCode
}
if ($Action -eq "Update") {
    $process = Start-Process -FilePath $updateExecutable -WorkingDirectory $projectRoot -Wait -PassThru
    exit $process.ExitCode
}

function Show-Message {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Title = "Subtitle Edit Bay"
    )

    if (-not $SuppressMessages -and $env:SUBTITLE_EDIT_BAY_SUPPRESS_MESSAGES -ne "1") {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($Message, $Title) | Out-Null
    }
}

function Test-CudaRepairRequired {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$PythonPath
    )

    $configPath = Join-Path $Root ".gui\runtime_config.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        $configPath = Join-Path $Root "assets\runtime_config.json"
    }
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        return $false
    }

    try {
        $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$config.shared.device -ne "cuda") {
            return $false
        }
    } catch {
        return $false
    }

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $true
    }

    try {
        & $PythonPath -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)" *> $null
        return $LASTEXITCODE -ne 0
    } catch {
        return $true
    }
}

$cudaRepairRequired = Test-CudaRepairRequired -Root $projectRoot -PythonPath $python
if ($ProbeCudaRepairOnly) {
    Write-Output $cudaRepairRequired.ToString().ToLowerInvariant()
    exit 0
}

if (-not (Test-SetupComplete)) {
    Show-Message "初回セットアップが必要です。セットアップ画面を開きます。"
    Start-Process -FilePath $setupExecutable -WorkingDirectory $projectRoot
    exit 0
}

if ($cudaRepairRequired) {
    Show-Message "GPU設定が選択されていますが、CUDA対応PyTorchが利用できません。`n`n実行環境の修復セットアップを開きます。完了後にアプリをもう一度起動してください。" "Subtitle Edit Bay - GPU環境の修復"
    Start-Process -FilePath $setupExecutable -WorkingDirectory $projectRoot
    exit 0
}

$ffmpegPathFile = Join-Path $projectRoot ".local\ffmpeg_path.txt"
if (Test-Path -LiteralPath $ffmpegPathFile -PathType Leaf) {
    $ffmpegDirectory = (Get-Content -LiteralPath $ffmpegPathFile -Raw -Encoding UTF8).Trim()
    if ($ffmpegDirectory -and (Test-Path -LiteralPath $ffmpegDirectory -PathType Container)) {
        $env:PATH = "$ffmpegDirectory;$env:PATH"
    }
}

$env:PYTHONUTF8 = "1"
$logDirectory = if ($LogDirectoryOverride) {
    [IO.Path]::GetFullPath($LogDirectoryOverride)
} else {
    Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs"
}
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$errorLog = Join-Path $logDirectory "latest-launch-error.log"

try {
    $process = Start-Process `
        -FilePath $pythonw `
        -ArgumentList @("-m", "src.gui") `
        -WorkingDirectory $projectRoot `
        -RedirectStandardError $errorLog `
        -PassThru `
        -Wait

    if ($process.ExitCode -ne 0) {
        Show-Message "アプリを起動できませんでした。`n`n初回セットアップ・修復を実行してください。`n診断ログ: $errorLog" "Subtitle Edit Bay - 起動エラー"
        exit $process.ExitCode
    }
}
catch {
    $_ | Out-String | Set-Content -LiteralPath $errorLog -Encoding UTF8
    Show-Message "アプリを起動できませんでした。`n`n初回セットアップ・修復を実行してください。`n診断ログ: $errorLog" "Subtitle Edit Bay - 起動エラー"
    exit 1
}
