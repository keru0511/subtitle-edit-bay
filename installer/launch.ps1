param(
    [ValidateSet("Launch", "Setup", "Update")]
    [string]$Action = "Launch",
    [switch]$ProbeSetupStateOnly,
    [switch]$ProbeSetupRunningOnly,
    [switch]$ProbeCudaRepairOnly,
    [switch]$SuppressMessages,
    [string]$ProjectRootOverride = "",
    [string]$PythonOverride = "",
    [string]$PythonwOverride = "",
    [string]$SetupExecutableOverride = "",
    [string]$LogDirectoryOverride = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = if ($ProjectRootOverride) { [IO.Path]::GetFullPath($ProjectRootOverride) } else { [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)) }
$setupStateScript = Join-Path $PSScriptRoot "setup_state.ps1"
if (-not (Test-Path -LiteralPath $setupStateScript -PathType Leaf)) {
    $setupStateScript = Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\setup_state.ps1"
}
if (-not (Test-Path -LiteralPath $setupStateScript -PathType Leaf)) { throw "The setup state helper is missing: $setupStateScript" }
. $setupStateScript
$statusPath = Join-Path $projectRoot ".local\setup-status.json"
$logs = if ($LogDirectoryOverride) { [IO.Path]::GetFullPath($LogDirectoryOverride) } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs" } else { Join-Path $projectRoot ".local\logs" }
New-Item -ItemType Directory -Path $logs -Force | Out-Null

function Resolve-ActiveRuntimeDirectory {
    param([Parameter(Mandatory = $true)][string]$Root)
    $manifestPath = Join-Path $Root ".local\runtime-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return $null }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $manifest.runtime_directory) { throw "runtime_directory is missing" }
        $resolved = [IO.Path]::GetFullPath((Join-Path $Root ([string]$manifest.runtime_directory)))
        $allowedRoot = [IO.Path]::GetFullPath((Join-Path $Root ".local\runtimes"))
        if (-not $resolved.StartsWith($allowedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "runtime_directory is outside .local\runtimes" }
        return $resolved
    } catch { throw "The active runtime manifest is invalid: $_" }
}

function Read-SetupStatus {
    if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Test-SetupComplete {
    $versionPath = Join-Path $projectRoot "VERSION"
    $status = Read-SetupStatus
    if (-not $status -or -not (Test-Path -LiteralPath $versionPath -PathType Leaf)) { return $false }
    $version = (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8).Trim()
    if ([string]$status.status -ne "success" -or [string]$status.app_version -ne $version) { return $false }
    if ($PythonOverride -and $PythonwOverride) {
        return (Test-Path -LiteralPath $PythonOverride -PathType Leaf) -and (Test-Path -LiteralPath $PythonwOverride -PathType Leaf)
    }
    try { $runtime = Resolve-ActiveRuntimeDirectory -Root $projectRoot } catch { return $false }
    return $runtime -and (Test-Path -LiteralPath (Join-Path $runtime "Scripts\python.exe") -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $runtime "Scripts\pythonw.exe") -PathType Leaf)
}

function Show-Message {
    param([Parameter(Mandatory = $true)][string]$Message, [string]$Title = "Subtitle Edit Bay")
    if ($env:SUBTITLE_EDIT_BAY_MESSAGE_PROBE) {
        $probePath = [IO.Path]::GetFullPath($env:SUBTITLE_EDIT_BAY_MESSAGE_PROBE)
        $probeDirectory = Split-Path -Parent $probePath
        if ($probeDirectory) { New-Item -ItemType Directory -Path $probeDirectory -Force | Out-Null }
        $record = [ordered]@{ title = $Title; message = $Message } | ConvertTo-Json -Compress
        Add-Content -LiteralPath $probePath -Value $record -Encoding UTF8
    }
    if (-not $SuppressMessages -and $env:SUBTITLE_EDIT_BAY_SUPPRESS_MESSAGES -ne "1") {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($Message, $Title) | Out-Null
    }
}

function Show-SetupFailure {
    $status = Read-SetupStatus
    $reason = if ($status -and $status.message) { [string]$status.message } else { "詳細はセットアップログを確認してください。" }
    Show-Message "セットアップに失敗しました。$([Environment]::NewLine)$([Environment]::NewLine)$reason$([Environment]::NewLine)$([Environment]::NewLine)ログ: $(Join-Path $logs 'setup.log')$([Environment]::NewLine)$([Environment]::NewLine)「初回セットアップ・修復」または --setup で再試行してください。" "Subtitle Edit Bay - セットアップエラー"
}

function Get-VerifiedSetupExitCode {
    param([Parameter(Mandatory = $true)][int]$ExitCode)
    if ($ExitCode -eq 0 -and -not (Test-SetupComplete)) { return 1 }
    return $ExitCode
}

function Test-CudaRepairRequired {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$PythonPath)
    $configPath = Join-Path $Root ".gui\runtime_config.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { $configPath = Join-Path $Root "assets\runtime_config.json" }
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { return $false }
    try { $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json; if ([string]$config.shared.device -ne "cuda") { return $false } } catch { return $false }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { return $true }
    try { & $PythonPath -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)" *> $null; return $LASTEXITCODE -ne 0 } catch { return $true }
}

function Wait-ForSetup {
    param(
        [Diagnostics.Process]$Process,
        [string]$Title = "Subtitle Edit Bay - セットアップ",
        [string]$InitialMessage = "セットアップを実行しています。完了までお待ちください。",
        [switch]$IgnoreSetupStatus
    )
    if ($SuppressMessages -or $env:SUBTITLE_EDIT_BAY_SUPPRESS_MESSAGES -eq "1") {
        while ((Test-SetupMutexHeld -ProjectRoot $projectRoot) -or ($Process -and -not $Process.HasExited)) { Start-Sleep -Milliseconds 100 }
        if ($Process) { $Process.WaitForExit() }
        return
    }
    Add-Type -AssemblyName PresentationFramework
    $window = New-Object Windows.Window
    $window.Title = $Title
    $window.Width = 520
    $window.Height = 190
    $window.WindowStartupLocation = "CenterScreen"
    $panel = New-Object Windows.Controls.StackPanel
    $panel.Margin = 20
    $message = New-Object Windows.Controls.TextBlock
    $message.TextWrapping = "Wrap"
    $message.Text = $InitialMessage
    $progress = New-Object Windows.Controls.ProgressBar
    $progress.IsIndeterminate = $true
    $progress.Height = 18
    $progress.Margin = "0,18,0,12"
    $logText = New-Object Windows.Controls.TextBlock
    $logText.Text = "ログ: $(Join-Path $logs 'setup.log')"
    [void]$panel.Children.Add($message)
    [void]$panel.Children.Add($progress)
    [void]$panel.Children.Add($logText)
    $window.Content = $panel
    $timer = New-Object Windows.Threading.DispatcherTimer
    $timer.Interval = [TimeSpan]::FromMilliseconds(250)
    $timer.Add_Tick({
        if (-not $IgnoreSetupStatus) {
            $status = Read-SetupStatus
            if ($status -and $status.stage) { $message.Text = [string]$status.stage }
        }
        if (-not (Test-SetupMutexHeld -ProjectRoot $projectRoot) -and (-not $Process -or $Process.HasExited)) {
            $timer.Stop()
            $window.Close()
        }
    })
    $timer.Start()
    [void]$window.ShowDialog()
    if ($Process) { $Process.WaitForExit() }
}

function Start-HiddenPowerShell {
    param([Parameter(Mandatory = $true)][string]$ScriptPath)
    $systemPowerShell = Join-Path ([Environment]::GetFolderPath("System")) "WindowsPowerShell\v1.0\powershell.exe"
    $stdout = Join-Path $logs "$([IO.Path]::GetFileNameWithoutExtension($ScriptPath)).log"
    $stderr = Join-Path $logs "$([IO.Path]::GetFileNameWithoutExtension($ScriptPath))-error.log"
    $arguments = @("-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $ScriptPath))
    return Start-Process -FilePath $systemPowerShell -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
}

function Invoke-Setup {
    if (Test-SetupMutexHeld -ProjectRoot $projectRoot) {
        Wait-ForSetup
        if (Test-SetupComplete) { return 0 } else { return 1 }
    }
    $startLease = Enter-SetupStartMutex -ProjectRoot $projectRoot
    if (-not $startLease.Acquired) {
        $startLease.Mutex.Dispose()
        throw "Timed out while coordinating setup startup."
    }
    try {
        if (Test-SetupMutexHeld -ProjectRoot $projectRoot) {
            Wait-ForSetup
            if (Test-SetupComplete) { return 0 } else { return 1 }
        }
        $process = if ($SetupExecutableOverride) {
            Start-Process -FilePath ([IO.Path]::GetFullPath($SetupExecutableOverride)) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
        } else {
            Start-HiddenPowerShell -ScriptPath (Join-Path $projectRoot "scripts\setup.ps1")
        }
        $startDeadline = [DateTime]::UtcNow.AddSeconds(10)
        while (-not (Test-SetupMutexHeld -ProjectRoot $projectRoot) -and -not $process.HasExited -and [DateTime]::UtcNow -lt $startDeadline) {
            Start-Sleep -Milliseconds 50
        }
    } finally {
        Exit-SetupMutex -Lease $startLease
    }
    Wait-ForSetup -Process $process
    if ($process.ExitCode -eq 32) {
        Wait-ForSetup
        if (Test-SetupComplete) { return 0 } else { return 1 }
    }
    return $process.ExitCode
}

if ($ProbeCudaRepairOnly) {
    $probePython = if ($PythonOverride) { [IO.Path]::GetFullPath($PythonOverride) } else {
        $probeRuntime = Resolve-ActiveRuntimeDirectory -Root $projectRoot
        if ($probeRuntime) { Join-Path $probeRuntime "Scripts\python.exe" } else { "" }
    }
    Write-Output (Test-CudaRepairRequired -Root $projectRoot -PythonPath $probePython).ToString().ToLowerInvariant()
    exit 0
}
if ($ProbeSetupRunningOnly) { if (Test-SetupMutexHeld -ProjectRoot $projectRoot) { exit 0 }; exit 3 }
if ($ProbeSetupStateOnly) { if (Test-SetupComplete) { exit 0 }; exit 3 }
if ($Action -eq "Setup") {
    $exitCode = Get-VerifiedSetupExitCode -ExitCode ([int](@(Invoke-Setup)[-1]))
    $completed = Test-SetupComplete
    if ($exitCode -eq 0 -and $completed) { Show-Message "セットアップが完了しました。Subtitle Edit Bayを起動できます。" }
    else { Show-SetupFailure }
    exit $exitCode
}
if ($Action -eq "Update") {
    $process = Start-HiddenPowerShell -ScriptPath (Join-Path $projectRoot "scripts\update.ps1")
    Wait-ForSetup -Process $process -Title "Subtitle Edit Bay - アップデート" -InitialMessage "アップデートを実行しています。完了までお待ちください。" -IgnoreSetupStatus
    if ($process.ExitCode -eq 0) { Show-Message "アップデートが完了しました。" } else { Show-Message "アップデートに失敗しました。ログを確認してください。" "Subtitle Edit Bay - アップデートエラー" }
    exit $process.ExitCode
}
if (-not (Test-SetupComplete)) {
    $exitCode = Get-VerifiedSetupExitCode -ExitCode ([int](@(Invoke-Setup)[-1]))
    if ($exitCode -ne 0) { Show-SetupFailure; exit $exitCode }
    if ($SetupExecutableOverride) { exit 0 }
}

$runtimeDirectory = Resolve-ActiveRuntimeDirectory -Root $projectRoot
$pythonw = if ($PythonwOverride) { [IO.Path]::GetFullPath($PythonwOverride) } else { Join-Path $runtimeDirectory "Scripts\pythonw.exe" }
$python = if ($PythonOverride) { [IO.Path]::GetFullPath($PythonOverride) } else { Join-Path $runtimeDirectory "Scripts\python.exe" }

$cudaRepairRequired = Test-CudaRepairRequired -Root $projectRoot -PythonPath $python
if ($cudaRepairRequired) {
    Show-Message "GPU設定に必要なCUDA runtimeを修復します。" "Subtitle Edit Bay - GPU環境の修復"
    $exitCode = Get-VerifiedSetupExitCode -ExitCode ([int](@(Invoke-Setup)[-1]))
    if ($exitCode -ne 0) { Show-SetupFailure; exit $exitCode }
    if ($SetupExecutableOverride) { exit 0 }
    $runtimeDirectory = Resolve-ActiveRuntimeDirectory -Root $projectRoot
    $pythonw = Join-Path $runtimeDirectory "Scripts\pythonw.exe"
}

$ffmpegPathFile = Join-Path $projectRoot ".local\ffmpeg_path.txt"
if (Test-Path -LiteralPath $ffmpegPathFile -PathType Leaf) {
    $ffmpegDirectory = (Get-Content -LiteralPath $ffmpegPathFile -Raw -Encoding UTF8).Trim()
    if ($ffmpegDirectory -and (Test-Path -LiteralPath $ffmpegDirectory -PathType Container)) { $env:PATH = "$ffmpegDirectory;$env:PATH" }
}
$env:PYTHONUTF8 = "1"
$errorLog = Join-Path $logs "latest-launch-error.log"
try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $pythonw
    $psi.Arguments = "-m src.gui"
    $psi.WorkingDirectory = $projectRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($psi)
    # Begin draining stderr before waiting. Waiting first can deadlock when the
    # child fills the redirected pipe and blocks while this launcher waits for
    # that same child to exit.
    $stderrTask = $process.StandardError.ReadToEndAsync()
    while (-not $process.HasExited) {
        Start-Sleep -Milliseconds 100
    }
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $stderrText = $stderrTask.GetAwaiter().GetResult()
    [IO.File]::WriteAllText($errorLog, $stderrText, [Text.UTF8Encoding]::new($false))
    if ($exitCode -ne 0) { Show-Message "アプリを起動できませんでした。診断ログ: $errorLog" "Subtitle Edit Bay - 起動エラー"; exit $exitCode }
} catch {
    $_ | Out-String | Set-Content -LiteralPath $errorLog -Encoding UTF8
    Show-Message "アプリを起動できませんでした。診断ログ: $errorLog" "Subtitle Edit Bay - 起動エラー"
    exit 1
}
