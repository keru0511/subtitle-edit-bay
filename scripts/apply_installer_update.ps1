param(
    [Parameter(Mandatory = $true)][string]$PackagePath,
    [Parameter(Mandatory = $true)][int]$ParentPid,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$ExpectedSha256,
    [Parameter(Mandatory = $true)][string]$ResultPath
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$correlationId = [Guid]::NewGuid().ToString("N")
$logRoot = Join-Path (Split-Path -Parent $ResultPath) "logs"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$helperLog = Join-Path $logRoot "update-$correlationId.log"
$installerLog = Join-Path $logRoot "installer-$correlationId.log"
$setupLog = Join-Path $logRoot "setup-$correlationId.log"
[IO.File]::WriteAllText($helperLog, "", [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($installerLog, "", [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($setupLog, "", [Text.UTF8Encoding]::new($false))

function Write-StepLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    Add-Content -LiteralPath $helperLog -Value ("{0:o} {1}" -f (Get-Date), $Message) -Encoding UTF8
}

function Write-UpdateResult {
    param([Parameter(Mandatory = $true)][hashtable]$Result)
    $parent = Split-Path -Parent $ResultPath
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $Result.correlation_id = $correlationId
    $Result.log = $helperLog
    $Result.installer_log = $installerLog
    $Result.setup_log = $setupLog
    $temporary = "$ResultPath.$([Guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary, ($Result | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $ResultPath -Force
}

function Test-RecoveryPreservedPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    $topLevel = ($RelativePath.Replace('\', '/') -split '/')[0]
    return @('.git', '.venv', '.local', '.gui', 'video_import', 'video_export', 'out', '__pycache__') -contains $topLevel
}

function Get-RecoveryRelativePath {
    param([string]$Root, [string]$FullPath)
    $resolvedRoot = (Get-Item -LiteralPath $Root).FullName.TrimEnd([char[]] "/\")
    return (Get-Item -LiteralPath $FullPath).FullName.Substring($resolvedRoot.Length).TrimStart([char[]] "/\")
}

function New-RecoverySnapshot {
    param([Parameter(Mandatory = $true)][string]$Root)
    # .local is preserved by Inno Setup and is on the runtime's volume. Moving
    # .venv into this recovery point is therefore an atomic directory rename.
    $snapshotRoot = Join-Path $Root (".local\update-recovery\" + $correlationId)
    $filesRoot = Join-Path $snapshotRoot "files"
    New-Item -ItemType Directory -Path $filesRoot -Force | Out-Null
    $entries = New-Object System.Collections.ArrayList
    foreach ($file in @(Get-ChildItem -LiteralPath $Root -Force -File -Recurse)) {
        $relative = Get-RecoveryRelativePath -Root $Root -FullPath $file.FullName
        if (Test-RecoveryPreservedPath -RelativePath $relative) { continue }
        $destination = Join-Path $filesRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        [void]$entries.Add($relative.Replace('\', '/'))
    }
    [IO.File]::WriteAllText(
        (Join-Path $snapshotRoot "manifest.json"),
        (($entries | Sort-Object -Unique | ConvertTo-Json -Compress) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    return $snapshotRoot
}

function Restore-RecoverySnapshot {
    param([Parameter(Mandatory = $true)][string]$SnapshotRoot)
    $manifestPath = Join-Path $SnapshotRoot "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "Recovery snapshot manifest is missing: $manifestPath" }
    $expected = @{}
    foreach ($entry in @((Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json))) {
        $relative = ([string]$entry).Replace('/', '\')
        if ($relative -and -not (Test-RecoveryPreservedPath -RelativePath $relative)) { $expected[$relative] = $true }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $InstallRoot -Force -File -Recurse)) {
        $relative = Get-RecoveryRelativePath -Root $InstallRoot -FullPath $file.FullName
        if (-not (Test-RecoveryPreservedPath -RelativePath $relative) -and -not $expected.ContainsKey($relative)) {
            Remove-Item -LiteralPath $file.FullName -Force
        }
    }
    foreach ($relative in $expected.Keys) {
        $source = Join-Path (Join-Path $SnapshotRoot "files") $relative
        $destination = Join-Path $InstallRoot $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Recovery snapshot file is missing: $relative" }
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

function Move-RuntimeToRecovery {
    param([Parameter(Mandatory = $true)][string]$SnapshotRoot)
    $runtime = Join-Path $InstallRoot ".venv"
    if (-not (Test-Path -LiteralPath $runtime -PathType Container)) { return $false }
    $runtimeRecovery = Join-Path $SnapshotRoot "runtime\.venv"
    New-Item -ItemType Directory -Path (Split-Path -Parent $runtimeRecovery) -Force | Out-Null
    Move-Item -LiteralPath $runtime -Destination $runtimeRecovery
    return $true
}

function New-RuntimeStateSnapshot {
    param([Parameter(Mandatory = $true)][string]$SnapshotRoot)
    $stateRoot = Join-Path $SnapshotRoot "runtime-state"
    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    $entries = @{}
    foreach ($relative in @(".gui\runtime_config.json", ".local\ffmpeg_path.txt")) {
        $source = Join-Path $InstallRoot $relative
        $entries[$relative] = Test-Path -LiteralPath $source -PathType Leaf
        if ($entries[$relative]) {
            $destination = Join-Path $stateRoot $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath $source -Destination $destination -Force
        }
    }
    [IO.File]::WriteAllText(
        (Join-Path $stateRoot "manifest.json"),
        ($entries | ConvertTo-Json -Compress),
        [Text.UTF8Encoding]::new($false)
    )
}

function Restore-RuntimeState {
    param([Parameter(Mandatory = $true)][string]$SnapshotRoot)
    $stateRoot = Join-Path $SnapshotRoot "runtime-state"
    $entries = Get-Content -LiteralPath (Join-Path $stateRoot "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($relative in @(".gui\runtime_config.json", ".local\ffmpeg_path.txt")) {
        $destination = Join-Path $InstallRoot $relative
        $wasPresent = [bool]($entries.PSObject.Properties[$relative].Value)
        if ($wasPresent) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath (Join-Path $stateRoot $relative) -Destination $destination -Force
        } elseif (Test-Path -LiteralPath $destination) {
            Remove-Item -LiteralPath $destination -Force
        }
    }
}

function Restore-Runtime {
    param([string]$SnapshotRoot, [bool]$HadRuntime)
    $runtime = Join-Path $InstallRoot ".venv"
    if (Test-Path -LiteralPath $runtime) { Remove-Item -LiteralPath $runtime -Recurse -Force }
    if ($HadRuntime) {
        $runtimeRecovery = Join-Path $SnapshotRoot "runtime\.venv"
        if (-not (Test-Path -LiteralPath $runtimeRecovery -PathType Container)) { throw "Runtime recovery directory is missing." }
        Move-Item -LiteralPath $runtimeRecovery -Destination $runtime
    }
}

function Get-PackageSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $getFileHash = Get-Command "Get-FileHash" -ErrorAction SilentlyContinue
    if ($getFileHash) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha256.ComputeHash([IO.File]::ReadAllBytes($Path)))).Replace('-', '').ToLowerInvariant() }
    finally { $sha256.Dispose() }
}

function Get-UpdateProcessIds {
    param([Parameter(Mandatory = $true)][int]$RootPid)
    if ($RootPid -le 0) { return @() }
    $ids = New-Object System.Collections.ArrayList
    [void]$ids.Add($RootPid)
    $nextPid = $RootPid
    while ($nextPid -gt 0) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $nextPid" -ErrorAction SilentlyContinue
        if (-not $process -or -not $process.ParentProcessId) { break }
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ParentProcessId)" -ErrorAction SilentlyContinue
        if (-not $parent) { break }
        $commandLine = [string]$parent.CommandLine
        $executable = [string]$parent.ExecutablePath
        if (($commandLine -notlike "*$InstallRoot*") -and ($executable -notlike "$InstallRoot*")) { break }
        if ([int]$parent.ProcessId -ne $PID) { [void]$ids.Add([int]$parent.ProcessId) }
        $nextPid = [int]$parent.ProcessId
    }
    return @($ids | Select-Object -Unique)
}

function Wait-ForUpdateProcesses {
    param([Parameter(Mandatory = $true)][int[]]$ProcessIds)
    $waitUntil = (Get-Date).AddSeconds(120)
    foreach ($processId in $ProcessIds) {
        while ((Get-Date) -lt $waitUntil -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 250 }
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) { throw "Update process $processId did not exit before the timeout." }
    }
}

function Wait-ForInstallLocks {
    $paths = @(
        (Join-Path $InstallRoot "SubtitleEditBayLauncher.exe"), (Join-Path $InstallRoot "SubtitleEditBay.exe"),
        (Join-Path $InstallRoot ".venv\Scripts\python.exe"), (Join-Path $InstallRoot ".venv\Scripts\pythonw.exe")
    )
    $waitUntil = (Get-Date).AddSeconds(120)
    foreach ($path in $paths) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        while ($true) {
            try { $stream = [IO.File]::Open($path, 'Open', 'Read', 'None'); $stream.Dispose(); break }
            catch {
                if ((Get-Date) -ge $waitUntil) { throw "Install file is still locked: $path" }
                Start-Sleep -Milliseconds 250
            }
        }
    }
}

function Resolve-RestartCommand {
    param([Parameter(Mandatory = $true)][string]$Root)
    foreach ($name in @("SubtitleEditBayLauncher.exe", "SubtitleEditBay.exe")) {
        $candidate = Join-Path $Root $name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return @{ FilePath = (Get-Item -LiteralPath $candidate).FullName; Arguments = @(); Mode = "native" }
        }
    }
    $restartScript = Join-Path $Root "scripts\launch.ps1"
    if (-not (Test-Path -LiteralPath $restartScript -PathType Leaf)) { throw "Restart launcher is missing below the installed root." }
    $powerShell = Get-Command "powershell.exe" -ErrorAction SilentlyContinue
    if (-not $powerShell) { throw "Windows PowerShell is required to restart the application fallback." }
    return @{
        FilePath = $powerShell.Source
        Arguments = @("-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ('"' + $restartScript + '"'))
        Mode = "powershell"
    }
}

function Start-RestartCommand {
    param([Parameter(Mandatory = $true)][hashtable]$Command)
    if (@($Command.Arguments).Count -gt 0) {
        Start-Process -FilePath $Command.FilePath -ArgumentList $Command.Arguments -WorkingDirectory $InstallRoot -WindowStyle Hidden
    } else {
        Start-Process -FilePath $Command.FilePath -WorkingDirectory $InstallRoot -WindowStyle Hidden
    }
}

function Invoke-RuntimeSetup {
    $setupScript = Join-Path $InstallRoot "scripts\setup.ps1"
    if (-not (Test-Path -LiteralPath $setupScript -PathType Leaf)) { throw "Updated runtime setup script is missing." }
    $powerShell = (Get-Command "powershell.exe" -ErrorAction Stop).Source
    $stderrLog = "$setupLog.stderr"
    $setup = Start-Process -FilePath $powerShell -ArgumentList @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ('"' + $setupScript + '"')
    ) -WorkingDirectory $InstallRoot -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $setupLog -RedirectStandardError $stderrLog
    if (Test-Path -LiteralPath $stderrLog) {
        Add-Content -LiteralPath $setupLog -Value (Get-Content -LiteralPath $stderrLog -Raw -ErrorAction SilentlyContinue) -Encoding UTF8
        Remove-Item -LiteralPath $stderrLog -Force -ErrorAction SilentlyContinue
    }
    if ($setup.ExitCode -ne 0) { throw "Runtime setup exited with code $($setup.ExitCode)." }
}

function Invoke-RuntimeValidation {
    $validationScript = Join-Path $InstallRoot "scripts\validate_runtime.ps1"
    if (-not (Test-Path -LiteralPath $validationScript -PathType Leaf)) { throw "Updated runtime validation script is missing." }
    $powerShell = (Get-Command "powershell.exe" -ErrorAction Stop).Source
    & $powerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $validationScript -InstallRoot $InstallRoot -LogPath $setupLog
    if ($LASTEXITCODE -ne 0) { throw "Updated runtime validation failed with code $LASTEXITCODE." }
}

$oldVersion = "development"
$recoveryRoot = ""
$hadRuntime = $false
$processIds = @(Get-UpdateProcessIds -RootPid $ParentPid)
try {
    $InstallRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
    $versionPath = Join-Path $InstallRoot "VERSION"
    if (Test-Path -LiteralPath $versionPath -PathType Leaf) { $oldVersion = (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8).Trim() }
    Write-StepLog "transaction=$correlationId old_version=$oldVersion install_root=$InstallRoot"
    if ($processIds.Count -gt 0) {
        Wait-ForUpdateProcesses -ProcessIds $processIds
    }
    Wait-ForInstallLocks
    if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) { throw "Downloaded installer package is missing." }
    if ((Get-PackageSha256 -Path $PackagePath) -ne $ExpectedSha256.ToLowerInvariant()) { throw "Installer package checksum does not match." }

    Write-StepLog "creating application and runtime recovery point"
    $recoveryRoot = New-RecoverySnapshot -Root $InstallRoot
    New-RuntimeStateSnapshot -SnapshotRoot $recoveryRoot
    $hadRuntime = Move-RuntimeToRecovery -SnapshotRoot $recoveryRoot
    $installerArguments = @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
        ('/DIR="' + $InstallRoot + '"'), ('/LOG="' + $installerLog + '"')
    )
    Write-StepLog "starting installer with explicit install root"
    $installer = Start-Process -FilePath $PackagePath -ArgumentList $installerArguments -WorkingDirectory $InstallRoot -WindowStyle Hidden -Wait -PassThru
    if ($installer.ExitCode -ne 0) { throw "Installer exited with code $($installer.ExitCode)." }

    if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf)) { throw "Updated VERSION file is missing." }
    $newVersion = (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8).Trim()
    if ($newVersion.TrimStart('v') -ne $ExpectedVersion.TrimStart('v')) { throw "Installed version $newVersion does not match $ExpectedVersion." }
    Write-StepLog "building a fresh runtime"
    Invoke-RuntimeSetup
    Invoke-RuntimeValidation
    $restartCommand = Resolve-RestartCommand -Root $InstallRoot

    Write-StepLog "validation passed; starting the post-update launcher"
    Start-RestartCommand -Command $restartCommand
    Write-StepLog "launcher started; committing transaction"
    if ($recoveryRoot -and (Test-Path -LiteralPath $recoveryRoot)) { Remove-Item -LiteralPath $recoveryRoot -Recurse -Force }
    Write-UpdateResult @{ status = "success"; old_version = $oldVersion; new_version = $newVersion; restart_mode = $restartCommand.Mode; process_ids = $processIds }
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-StepLog "failure=$message; starting rollback"
    $rollbackRestored = $false
    $rollbackError = ""
    $rollbackRestarted = $false
    if ($recoveryRoot -and (Test-Path -LiteralPath $recoveryRoot)) {
        try {
            Restore-RecoverySnapshot -SnapshotRoot $recoveryRoot
            Restore-Runtime -SnapshotRoot $recoveryRoot -HadRuntime $hadRuntime
            Restore-RuntimeState -SnapshotRoot $recoveryRoot
            $restoredVersion = Get-Content -LiteralPath (Join-Path $InstallRoot "VERSION") -Raw -Encoding UTF8
            if ($restoredVersion.Trim().TrimStart('v') -ne $oldVersion.Trim().TrimStart('v')) { throw "Restored VERSION does not match the previous version." }
            $rollbackRestored = $true
            Start-RestartCommand -Command (Resolve-RestartCommand -Root $InstallRoot)
            $rollbackRestarted = $true
            Write-StepLog "rollback restored and restarted the previous version"
        } catch { $rollbackError = $_.Exception.Message; Write-StepLog "rollback failure=$rollbackError" }
    } else {
        # Verification can fail after the GUI has exited but before any files
        # were changed. The old installation is already the recovery state.
        try {
            $rollbackRestored = $true
            Start-RestartCommand -Command (Resolve-RestartCommand -Root $InstallRoot)
            $rollbackRestarted = $true
            Write-StepLog "pre-install failure restarted the unchanged version"
        } catch { $rollbackError = $_.Exception.Message; Write-StepLog "restart failure=$rollbackError" }
    }
    if ($rollbackRestored) {
        Write-UpdateResult @{ status = "rollback"; rollback_restored = $true; rollback_restarted = $rollbackRestarted; old_version = $oldVersion; new_version = $oldVersion; message = $message; recovery_snapshot = $recoveryRoot; process_ids = $processIds }
    } else {
        Write-UpdateResult @{ status = "rollback_failed"; rollback_restored = $false; rollback_restarted = $false; old_version = $oldVersion; new_version = $oldVersion; message = $message; rollback_error = $rollbackError; recovery_snapshot = $recoveryRoot; process_ids = $processIds }
    }
    exit 1
}
