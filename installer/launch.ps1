param(
    [switch]$ProbeCudaRepairOnly,
    [string]$ProjectRootOverride = "",
    [string]$PythonOverride = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = if ($ProjectRootOverride) {
    [IO.Path]::GetFullPath($ProjectRootOverride)
} else {
    [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
}
$pythonw = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
$python = if ($PythonOverride) {
    [IO.Path]::GetFullPath($PythonOverride)
} else {
    Join-Path $projectRoot ".venv\Scripts\python.exe"
}

function Show-Message {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Title = "Subtitle Edit Bay"
    )

    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($Message, $Title) | Out-Null
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

if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    Show-Message "初回セットアップが必要です。セットアップ画面を開きます。"
    Start-Process -FilePath (Join-Path $projectRoot "setup.bat") -WorkingDirectory $projectRoot
    exit 0
}

if ($cudaRepairRequired) {
    Show-Message "GPU設定が選択されていますが、CUDA対応PyTorchが利用できません。`n`n実行環境の修復セットアップを開きます。完了後にアプリをもう一度起動してください。" "Subtitle Edit Bay - GPU環境の修復"
    Start-Process -FilePath (Join-Path $projectRoot "setup.bat") -WorkingDirectory $projectRoot
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
$logDirectory = Join-Path $env:LOCALAPPDATA "SubtitleEditBay\logs"
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
    }
}
catch {
    $_ | Out-String | Set-Content -LiteralPath $errorLog -Encoding UTF8
    Show-Message "アプリを起動できませんでした。`n`n初回セットアップ・修復を実行してください。`n診断ログ: $errorLog" "Subtitle Edit Bay - 起動エラー"
}
