$ErrorActionPreference = "Stop"

$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$pythonw = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"

function Show-Message {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Title = "Subtitle Edit Bay"
    )

    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($Message, $Title) | Out-Null
}

if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    Show-Message "初回セットアップが必要です。セットアップ画面を開きます。"
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
