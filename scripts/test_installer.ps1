param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$')][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$InstallDirectory,
    [ValidateRange(10, 900)][int]$InstallerTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$installer = [IO.Path]::GetFullPath($InstallerPath)
$installDir = [IO.Path]::GetFullPath($InstallDirectory)
$testRoot = [IO.Path]::GetDirectoryName($installDir)
$logPath = Join-Path $testRoot "subtitle-edit-bay-install.log"
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw "Installer is missing: $installer" }

function Write-InstallerDiagnostics {
    param(
        [Parameter(Mandatory = $true)][string]$Scenario,
        [Parameter(Mandatory = $true)][Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$LogPath
    )
    Write-Host "INSTALLER_DIAGNOSTICS scenario=$Scenario root_pid=$($Process.Id) log=$LogPath"
    if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
        Write-Host "--- Inno Setup log tail ($Scenario) ---"
        Get-Content -LiteralPath $LogPath -Tail 250 -ErrorAction Continue | ForEach-Object { Write-Host $_ }
    } else {
        Write-Host "Inno Setup log was not created for scenario '$Scenario'."
    }
    try {
        $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $included = [Collections.Generic.HashSet[uint32]]::new()
        [void]$included.Add([uint32]$Process.Id)
        do {
            $added = $false
            foreach ($candidate in $allProcesses) {
                if ($included.Contains([uint32]$candidate.ParentProcessId) -and
                    $included.Add([uint32]$candidate.ProcessId)) {
                    $added = $true
                }
            }
        } while ($added)
        foreach ($candidate in ($allProcesses | Where-Object {
            $included.Contains([uint32]$_.ProcessId) -or $_.Name -like "SubtitleEditBay-Setup*"
        } | Sort-Object ProcessId)) {
            Write-Host ("PROCESS pid={0} ppid={1} name={2} command={3}" -f `
                $candidate.ProcessId, $candidate.ParentProcessId, $candidate.Name, $candidate.CommandLine)
        }
    } catch {
        Write-Warning "Could not collect Installer process diagnostics: $_"
    }
}

function Invoke-InstallerScenario {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$LogPath
    )
    Write-Host "INSTALLER_SCENARIO_START name=$Name timeout_seconds=$InstallerTimeoutSeconds log=$LogPath"
    $process = Start-Process -FilePath $installer -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit($InstallerTimeoutSeconds * 1000)) {
        Write-InstallerDiagnostics -Scenario $Name -Process $process -LogPath $LogPath
        & taskkill.exe /PID $process.Id /T /F 2>&1 | ForEach-Object { Write-Host $_ }
        throw "Installer scenario '$Name' timed out after $InstallerTimeoutSeconds seconds."
    }
    Write-Host "INSTALLER_SCENARIO_END name=$Name exit_code=$($process.ExitCode)"
    if ($process.ExitCode -ne 0 -and (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        Get-Content -LiteralPath $LogPath -Tail 250 -ErrorAction Continue | ForEach-Object { Write-Host $_ }
    }
    return $process
}

$install = Invoke-InstallerScenario -Name "base-install" -LogPath $logPath -Arguments @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$installDir", "/LOG=$logPath"
)
if ($install.ExitCode -ne 0) {
    throw "Installer exited with code $($install.ExitCode)."
}
foreach ($path in @(
    "SubtitleEditBayLauncher.exe", "src\gui.py", "src\ui\Main.qml", "scripts\launch.ps1",
    "scripts\setup.ps1", "scripts\setup_state.ps1", "scripts\runtime_activation.ps1",
    "scripts\runtime_contract.py", "scripts\windows_path_identity.ps1", "scripts\windows_signing_identity.ps1", "runtime\runtime-contract.json",
    "runtime\requirements-windows-cpu.lock", "runtime\requirements-windows-cu128.lock", "VERSION"
)) {
    $candidate = Join-Path $installDir $path
    if (-not (Test-Path -LiteralPath $candidate)) { throw "Installed file is missing: $candidate" }
}
$expectedInstalledVersion = $ExpectedVersion.Substring(1)
$installedVersion = (Get-Content -LiteralPath (Join-Path $installDir "VERSION") -Raw).Trim()
if ($installedVersion -ne $expectedInstalledVersion) { throw "Installed VERSION mismatch: expected=$expectedInstalledVersion actual=$installedVersion" }

# Exercise the Installer-owned persistence path with non-ASCII data. The repair
# setup must consume exactly the UTF-8 request written by Inno Setup.
$migrationSource = Join-Path $testRoot "旧ワークスペース"
$migrationInstallDir = Join-Path $testRoot "migration-probe-install"
New-Item -ItemType Directory -Path @(
    (Join-Path $migrationSource "src"),
    (Join-Path $migrationSource ".gui"),
    (Join-Path $migrationSource "assets"),
    (Join-Path $migrationSource ".venv\Scripts"),
    (Join-Path $migrationSource "video_import"),
    (Join-Path $migrationSource "video_export"),
    (Join-Path $migrationSource "out"),
    (Join-Path $migrationSource "project"),
    (Join-Path $migrationSource ".cache\pip")
) -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $migrationSource "setup.bat"), "setup")
[IO.File]::WriteAllText((Join-Path $migrationSource "start.bat"), "start")
[IO.File]::WriteAllText(
    (Join-Path $migrationSource ".gui\runtime_config.json"),
    '{"shared":{"device":"cuda","compute_type":"float16","language":"ja"},"craig_pipeline":{"video_codec":"h264_nvenc"}}'
)
[IO.File]::WriteAllText(
    (Join-Path $migrationSource "assets\speaker_colors.json"),
    '{"speakers":{"speaker-a":"#12ABEF"},"files":{}}'
)
$legacyVenvSentinel = Join-Path $migrationSource ".venv\Scripts\python.exe"
$legacyMedia = Join-Path $migrationSource "video_import\capture.mp4"
$legacyOutput = Join-Path $migrationSource "video_export\render.mp4"
$legacyProject = Join-Path $migrationSource "project\episode.seb-project.json"
$legacyCache = Join-Path $migrationSource ".cache\pip\download.whl"
[IO.File]::WriteAllText($legacyVenvSentinel, "legacy runtime must never execute")
[IO.File]::WriteAllBytes($legacyMedia, [Text.Encoding]::UTF8.GetBytes("media-fixture"))
[IO.File]::WriteAllBytes($legacyOutput, [Text.Encoding]::UTF8.GetBytes("output-fixture"))
[IO.File]::WriteAllText($legacyProject, "{}")
[IO.File]::WriteAllBytes($legacyCache, [Text.Encoding]::UTF8.GetBytes("cache-fixture"))
$legacyVenvBefore = [IO.File]::ReadAllText($legacyVenvSentinel)
$legacyMediaBefore = [IO.File]::ReadAllBytes($legacyMedia)
$legacyOutputBefore = [IO.File]::ReadAllBytes($legacyOutput)
$legacyProjectBefore = [IO.File]::ReadAllBytes($legacyProject)
$legacyCacheBefore = [IO.File]::ReadAllBytes($legacyCache)
$migrationInstallLog = Join-Path $testRoot "subtitle-edit-bay-migration-install.log"
$migrationInstall = Invoke-InstallerScenario -Name "migration-install" -LogPath $migrationInstallLog -Arguments @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$migrationInstallDir",
    "/TASKS=legacymigration", "/LEGACYWORKSPACE=$migrationSource", "/LOG=$migrationInstallLog"
)
if ($migrationInstall.ExitCode -ne 0) { throw "Installer UTF-8 migration probe failed with code $($migrationInstall.ExitCode)." }
$pendingMigrationPath = Join-Path $migrationInstallDir ".local\migration\pending-request.json"
$pendingMigration = Get-Content -LiteralPath $pendingMigrationPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([IO.Path]::GetFullPath([string]$pendingMigration.source) -ne [IO.Path]::GetFullPath($migrationSource)) {
    throw "Installer did not preserve the Japanese migration source as UTF-8."
}
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$pendingProbeOutput = & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `
    (Join-Path $migrationInstallDir "scripts\setup.ps1") -ProbePendingMigrationOnly
if ($LASTEXITCODE -ne 0) { throw "Setup could not reload the Installer-owned pending migration request." }
$pendingProbe = ($pendingProbeOutput | Select-Object -Last 1) | ConvertFrom-Json
if ([IO.Path]::GetFullPath([string]$pendingProbe.source) -ne [IO.Path]::GetFullPath($migrationSource)) {
    throw "Setup changed the Japanese migration source while reloading it."
}

# Inno Setup remembers task selections for a stable AppId. A later silent
# application update without migration arguments must not restore the one-shot
# migration task and fail because LEGACYWORKSPACE is absent.
$migrationUpdateLog = Join-Path $testRoot "subtitle-edit-bay-migration-update.log"
$migrationUpdate = Invoke-InstallerScenario -Name "migration-free-update" -LogPath $migrationUpdateLog -Arguments @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$migrationInstallDir",
    "/LOG=$migrationUpdateLog"
)
if ($migrationUpdate.ExitCode -ne 0) {
    Get-Content -LiteralPath $migrationUpdateLog -ErrorAction SilentlyContinue
    throw "Silent update restored the one-shot migration task and failed with code $($migrationUpdate.ExitCode)."
}
$pendingAfterUpdate = Get-Content -LiteralPath $pendingMigrationPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([IO.Path]::GetFullPath([string]$pendingAfterUpdate.source) -ne [IO.Path]::GetFullPath($migrationSource)) {
    throw "Silent update changed the pending migration request."
}

# A junction alias for the destination must fail before the Installer writes
# product files or setup has a chance to mutate the legacy runtime.
$junctionDestination = Join-Path $testRoot "junction-destination"
$junctionAlias = Join-Path $testRoot "junction-source-alias"
New-Item -ItemType Directory -Path (Join-Path $junctionDestination "src") -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $junctionDestination "setup.bat"), "legacy-setup")
[IO.File]::WriteAllText((Join-Path $junctionDestination "start.bat"), "legacy-start")
$legacySentinel = Join-Path $junctionDestination "legacy-sentinel.txt"
[IO.File]::WriteAllText($legacySentinel, "unchanged")
New-Item -ItemType Junction -Path $junctionAlias -Target $junctionDestination | Out-Null
$junctionInstallLog = Join-Path $testRoot "subtitle-edit-bay-junction-rejection.log"
$junctionInstall = Invoke-InstallerScenario -Name "junction-rejection" -LogPath $junctionInstallLog -Arguments @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$junctionDestination",
    "/TASKS=legacymigration", "/LEGACYWORKSPACE=$junctionAlias", "/LOG=$junctionInstallLog"
)
if ($junctionInstall.ExitCode -eq 0) { throw "Installer accepted its destination through a junction alias." }
$junctionLogText = Get-Content -LiteralPath $junctionInstallLog -Raw -ErrorAction Stop
if ($junctionLogText -notmatch "SILENT_MIGRATION_REJECTION") {
    throw "Silent junction rejection did not record its pre-install validation failure."
}
if ((Get-Content -LiteralPath $legacySentinel -Raw) -ne "unchanged") { throw "Installer changed legacy data before junction rejection." }
if (Test-Path -LiteralPath (Join-Path $junctionDestination "src\gui.py")) { throw "Installer copied product files before junction rejection." }

function Invoke-CleanLauncherProbe {
    param(
        [Parameter(Mandatory = $true)][string]$LauncherPath,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$ProbeLogPath
    )

    # The installer smoke job builds with MSVC, but the product launcher must
    # start without inheriting a developer toolchain or its PATH entries.
    $systemRoot = [Environment]::GetEnvironmentVariable("SystemRoot", "Machine")
    if (-not $systemRoot) { $systemRoot = $env:SystemRoot }
    $systemDrive = [Environment]::GetEnvironmentVariable("SystemDrive", "Machine")
    if (-not $systemDrive) { $systemDrive = "C:" }
    if (-not $systemRoot) { throw "SystemRoot is required for the clean launcher probe." }

    $cleanRoot = Join-Path $testRoot "clean-launcher-environment"
    $cleanLocalAppData = Join-Path $cleanRoot "LocalAppData"
    $cleanAppData = Join-Path $cleanRoot "AppData"
    $cleanUserProfile = Join-Path $cleanRoot "UserProfile"
    New-Item -ItemType Directory -Path $cleanLocalAppData, $cleanAppData, $cleanUserProfile -Force | Out-Null

    # Keep normal Windows shell/module variables so Windows PowerShell can
    # initialize deterministically, but remove every Visual Studio/MSVC/SDK
    # variable and PATH entry. The launcher is therefore tested without the
    # development toolchain while retaining only normal OS process context.
    $toolchainPattern = "(?i)(Visual Studio|MSVC|VCTools|Windows Kits|WindowsSdk|MSBuild|DevEnv)"
    $cleanEnvironment = @{}
    foreach ($entry in [Environment]::GetEnvironmentVariables("Process").GetEnumerator()) {
        if ($entry.Key -match "^(?i:VSCMD|VSINSTALL|VCINSTALL|VCTools|VisualStudioVersion|WindowsSDKVersion|WindowsSdkDir|UniversalCRTSdkDir|UCRTVersion|DevEnvDir|MSBuild|__VSCMD|INCLUDE|LIB|LIBPATH|VSLANG|CL|LINK)$") {
            continue
        }
        if ($entry.Key -eq "Path" -or $entry.Key -eq "PSModulePath") {
            continue
        }
        $cleanEnvironment[$entry.Key] = [string]$entry.Value
    }
    $cleanEnvironment["SystemRoot"] = $systemRoot
    $cleanEnvironment["WINDIR"] = $systemRoot
    $cleanEnvironment["SystemDrive"] = $systemDrive
    $cleanEnvironment["ComSpec"] = Join-Path $systemRoot "System32\cmd.exe"
    $cleanEnvironment["Path"] = @(
        (Join-Path $systemRoot "System32"),
        $systemRoot,
        (Join-Path $systemRoot "System32\Wbem"),
        (Join-Path $systemRoot "System32\WindowsPowerShell\v1.0")
    ) -join ";"
    $modulePaths = @(
        [Environment]::GetEnvironmentVariable("PSModulePath", "Machine"),
        [Environment]::GetEnvironmentVariable("PSModulePath", "User")
    ) |
        Where-Object { $_ } |
        ForEach-Object { $_ -split ";" } |
        Where-Object { $_ -and $_ -notmatch $toolchainPattern } |
        Select-Object -Unique
    $cleanEnvironment["PSModulePath"] = $modulePaths -join ";"
    $cleanEnvironment["TEMP"] = $cleanRoot
    $cleanEnvironment["TMP"] = $cleanRoot
    $cleanEnvironment["LOCALAPPDATA"] = $cleanLocalAppData
    $cleanEnvironment["APPDATA"] = $cleanAppData
    $cleanEnvironment["USERPROFILE"] = $cleanUserProfile

    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $LauncherPath
    $psi.Arguments = "--probe-setup"
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.EnvironmentVariables.Clear()
    foreach ($entry in $cleanEnvironment.GetEnumerator()) {
        $psi.EnvironmentVariables[$entry.Key] = [string]$entry.Value
    }

    Write-Host "CLEAN_LAUNCHER_PROBE_START launcher=$LauncherPath"
    try {
        $process = [Diagnostics.Process]::Start($psi)
    } catch {
        throw "Clean launcher probe could not start the product EXE: $_"
    }
    if (-not $process.WaitForExit(30000)) {
        try { $process.Kill() } catch {}
        $process.WaitForExit()
        throw "Clean launcher probe timed out after 30 seconds."
    }
    $exitCode = $process.ExitCode
    if ($exitCode -ne 3) {
        throw "Clean launcher probe returned $exitCode; expected setup-required exit code 3."
    }
    [IO.File]::WriteAllText(
        $ProbeLogPath,
        "exit_code=$exitCode" + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Write-Host "CLEAN_LAUNCHER_PROBE_END exit_code=$exitCode log=$ProbeLogPath"
}

$launcher = Join-Path $installDir "SubtitleEditBayLauncher.exe"
$cleanLauncherProbeLog = Join-Path $testRoot "clean-launcher-probe.txt"
Invoke-CleanLauncherProbe -LauncherPath $launcher -WorkingDirectory $installDir -ProbeLogPath $cleanLauncherProbeLog

$hookPath = Join-Path $testRoot "installer-e2e-setup-hook.ps1"
$hookCount = Join-Path $testRoot "installer-e2e-hook-count.txt"
$hookStarted = Join-Path $testRoot "installer-e2e-hook-started.txt"
$hookGate = Join-Path $testRoot "installer-e2e-hook-gate.txt"
$messageProbe = Join-Path $testRoot "installer-e2e-messages.jsonl"
$smokeResult = Join-Path $testRoot "installed-gui-smoke.json"
$hook = @'
param(
    [Parameter(Mandatory = $true)][string]$Phase,
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$RuntimeProfile
)
if ($Phase -ne "BeforeRuntimeBuild") { throw "Unexpected setup test hook phase: $Phase" }
Add-Content -LiteralPath $env:SUBTITLE_EDIT_BAY_HOOK_COUNT -Value "run" -Encoding ascii
[IO.File]::WriteAllText($env:SUBTITLE_EDIT_BAY_HOOK_STARTED, "started")
if ($env:SUBTITLE_EDIT_BAY_HOOK_MODE -eq "wait") {
    $deadline = [DateTime]::UtcNow.AddMinutes(2)
    while (-not (Test-Path -LiteralPath $env:SUBTITLE_EDIT_BAY_HOOK_GATE)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw "Timed out waiting for the E2E setup hook gate." }
        Start-Sleep -Milliseconds 100
    }
}
if ($env:SUBTITLE_EDIT_BAY_HOOK_MODE -eq "fail") {
    Write-Error "Synthetic setup hook failure"
    exit 17
}
'@
[IO.File]::WriteAllText($hookPath, $hook, (New-Object Text.UTF8Encoding($false)))

$env:SUBTITLE_EDIT_BAY_SETUP_TEST_HOOK = $hookPath
$env:SUBTITLE_EDIT_BAY_HOOK_COUNT = $hookCount
$env:SUBTITLE_EDIT_BAY_HOOK_STARTED = $hookStarted
$env:SUBTITLE_EDIT_BAY_HOOK_GATE = $hookGate
$env:SUBTITLE_EDIT_BAY_HOOK_MODE = "wait"
$env:SUBTITLE_EDIT_BAY_SUPPRESS_MESSAGES = "1"
$env:SUBTITLE_EDIT_BAY_MESSAGE_PROBE = $messageProbe
$env:SUBTITLE_EDIT_BAY_STARTUP_SMOKE_RESULT = $smokeResult
try {
    # Product EXE -> launch.ps1 -> real setup.ps1. Keep the pre-build hook paused,
    # then prove normal launch and an explicit repair both attach to that one
    # setup instead of starting another production dependency build.
    # Pass the migration source only to the explicit setup request. Leaving it
# in the parent environment would also make a normal product launch carry
# a one-shot migration request after the setup mutex is released.
$first = Start-Process -FilePath $launcher -ArgumentList @(
    "--setup", "--migration-source", ('"{0}"' -f $migrationSource)
) -WorkingDirectory $installDir -PassThru
    $deadline = [DateTime]::UtcNow.AddMinutes(2)
    while (-not (Test-Path -LiteralPath $hookStarted) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (-not (Test-Path -LiteralPath $hookStarted)) {
        Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs\setup.log") -ErrorAction SilentlyContinue
        Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs\setup-error.log") -ErrorAction SilentlyContinue
        throw "The real setup path did not reach the pre-build hook."
    }
    $runningStatus = Get-Content -LiteralPath (Join-Path $installDir ".local\setup-status.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($runningStatus.status -ne "running" -or -not $runningStatus.process_id) { throw "The real setup did not publish its running state." }
    $runningProbe = Start-Process -FilePath $launcher -ArgumentList "--probe-setup-running" -WorkingDirectory $installDir -Wait -PassThru
    if ($runningProbe.ExitCode -ne 0) { throw "The process-aware setup lock was not observable." }
    Remove-Item "Env:SUBTITLE_EDIT_BAY_MIGRATION_SOURCE" -ErrorAction SilentlyContinue
    $second = Start-Process -FilePath $launcher -ArgumentList "--setup" -WorkingDirectory $installDir -PassThru
    $normal = Start-Process -FilePath $launcher -WorkingDirectory $installDir -PassThru
    Start-Sleep -Seconds 2
    $runsBeforeRelease = @(Get-Content -LiteralPath $hookCount).Count
    if ($runsBeforeRelease -ne 1) { throw "Concurrent product launches started $runsBeforeRelease dependency builds." }
    [IO.File]::WriteAllText($hookGate, "continue")
    foreach ($process in @($first, $second, $normal)) {
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Concurrent product path exited with code $($process.ExitCode)." }
    }
    $hookRuns = @(Get-Content -LiteralPath $hookCount).Count
    if ($hookRuns -ne 1) { throw "Expected one production dependency build, found $hookRuns." }
    $stoppedProbe = Start-Process -FilePath $launcher -ArgumentList "--probe-setup-running" -WorkingDirectory $installDir -Wait -PassThru
    if ($stoppedProbe.ExitCode -ne 3) { throw "The setup lock was not released after completion." }

    $statusPath = Join-Path $installDir ".local\setup-status.json"
    $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($status.status -ne "success" -or $status.app_version -ne $expectedInstalledVersion -or -not $status.process_id) {
        throw "The real setup did not publish a valid success record: $($status | ConvertTo-Json -Compress)"
    }
    $runtimeManifest = Get-Content -LiteralPath (Join-Path $installDir ".local\runtime-manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $runtimeDirectory = [IO.Path]::GetFullPath((Join-Path $installDir ([string]$runtimeManifest.runtime_directory)))
    $runtimePython = Join-Path $runtimeDirectory "Scripts\python.exe"
    $venvPip = Join-Path $runtimeDirectory "Scripts\pip.exe"
    & $venvPip --version
    if ($LASTEXITCODE -ne 0) { throw "The activated runtime pip command is broken." }

    # The real Installer setup consumed the BAT/ZIP fixture above through
    # src.installer_migration_entrypoint. Verify the
    # complete first-run contract on Windows: conservative CPU/NVENC repair,
    # speaker colors, reference-only project/media/output handling, no legacy
    # runtime execution, and an explicit post-success cleanup plan that has not
    # deleted anything yet.
    Remove-Item "Env:SUBTITLE_EDIT_BAY_MIGRATION_SOURCE" -ErrorAction SilentlyContinue
    $migratedConfig = Get-Content -LiteralPath (Join-Path $installDir ".gui\runtime_config.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($migratedConfig.shared.device -ne "cpu" -or $migratedConfig.shared.compute_type -ne "int8") {
        throw "CUDA migration fixture was not corrected to the CPU runtime."
    }
    if ($migratedConfig.craig_pipeline.video_codec -ne "libx264") {
        throw "NVENC migration fixture was not corrected to libx264."
    }
    $migratedColors = Get-Content -LiteralPath (Join-Path $installDir "assets\speaker_colors.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($migratedColors.speakers.'speaker-a' -ne "#12ABEF") { throw "Speaker colors were not migrated." }
    $migrationResultPath = Join-Path $installDir ".local\migration\latest-result.json"
    if (-not (Test-Path -LiteralPath $migrationResultPath -PathType Leaf)) { throw "Migration result diagnostic is missing." }
    $migrationResult = Get-Content -LiteralPath $migrationResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $expectedReferences = @(
        (Split-Path -Parent $legacyMedia),
        (Split-Path -Parent $legacyOutput),
        (Split-Path -Parent $legacyProject)
    )
    foreach ($reference in $expectedReferences) {
        if (-not @($migrationResult.workspace_references | Where-Object { [IO.Path]::GetFullPath([string]$_) -eq [IO.Path]::GetFullPath($reference) })) {
            throw "Migration result did not retain the workspace reference: $reference"
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $installDir ".gui\legacy_workspaces.json") -PathType Leaf)) {
        throw "Migration did not register the legacy workspace reference."
    }
    $cleanupById = @{}
    foreach ($candidate in @($migrationResult.cleanup_candidates)) { $cleanupById[[string]$candidate.candidate_id] = $candidate }
    if (-not $cleanupById.ContainsKey("legacy:.venv")) { throw "Post-success cleanup did not expose the old .venv candidate." }
    if ($cleanupById["legacy:.venv"].state -ne "removable_after_success") {
        throw "Old .venv cleanup candidate was not classified after success."
    }
    if ((Get-Content -LiteralPath $legacyVenvSentinel -Raw) -ne $legacyVenvBefore) { throw "The old .venv was executed or changed." }
    if ([Convert]::ToBase64String($legacyMediaBefore) -ne [Convert]::ToBase64String([IO.File]::ReadAllBytes($legacyMedia))) { throw "Legacy media was changed." }
    if ([Convert]::ToBase64String($legacyOutputBefore) -ne [Convert]::ToBase64String([IO.File]::ReadAllBytes($legacyOutput))) { throw "Legacy output was changed." }
    if ([Convert]::ToBase64String($legacyProjectBefore) -ne [Convert]::ToBase64String([IO.File]::ReadAllBytes($legacyProject))) { throw "Legacy project was changed." }
    if ([Convert]::ToBase64String($legacyCacheBefore) -ne [Convert]::ToBase64String([IO.File]::ReadAllBytes($legacyCache))) { throw "Legacy cache was changed." }
    if (-not (Test-Path -LiteralPath $legacyVenvSentinel -PathType Leaf)) { throw "Unconfirmed cleanup removed the old .venv." }
    if ($runtimeDirectory.StartsWith(
            [IO.Path]::GetFullPath((Join-Path $migrationSource ".venv")),
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Installer runtime reused the legacy .venv."
    }

    # A verified external pip cache may be reused only when the caller passes
    # its exact path and candidate identity. This keeps the cache decision in
    # the #362 core while proving the representative Windows fixture path.
    $externalCache = Join-Path $testRoot "verified-pip-cache"
    New-Item -ItemType Directory -Path $externalCache -Force | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $externalCache "download.whl"), [Text.Encoding]::UTF8.GetBytes("verified-cache"))
    $cacheProbeCode = @'
import json
import sys
from pathlib import Path
from src.legacy_migration import CacheCleanupOptions, build_cache_cleanup_plan, build_legacy_inventory
inventory = build_legacy_inventory(Path(sys.argv[1]))
plan = build_cache_cleanup_plan(
    inventory,
    options=CacheCleanupOptions(
        migration_completed=True,
        cache_paths={"pip-user-cache": Path(sys.argv[2])},
        reusable_cache_ids=("pip-user-cache",),
    ),
)
print(json.dumps({entry.candidate_id: entry.state for entry in plan.entries}, sort_keys=True))
'@
    Push-Location $installDir
    try {
        $cacheProbeOutput = & $runtimePython -c $cacheProbeCode $migrationSource $externalCache
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) { throw "Verified cache reuse probe failed." }
    $cacheProbe = ($cacheProbeOutput | Select-Object -Last 1) | ConvertFrom-Json
    if ($cacheProbe.'pip-user-cache' -ne "reuse") { throw "Verified pip cache was not classified as reusable." }

    # After the independent runtime has been verified, deleting the old venv
    # must not affect Installer startup. The earlier assertions prove that no
    # cleanup happened without a per-candidate confirmation.
    Remove-Item -LiteralPath (Join-Path $migrationSource ".venv") -Recurse -Force
    $postCleanupProbe = Start-Process -FilePath $launcher -ArgumentList "--probe-setup" -WorkingDirectory $installDir -Wait -PassThru
    if ($postCleanupProbe.ExitCode -ne 0) { throw "Installer did not remain launchable after old .venv cleanup." }

    $readyProbe = Start-Process -FilePath $launcher -ArgumentList "--probe-setup" -WorkingDirectory $installDir -Wait -PassThru
    if ($readyProbe.ExitCode -ne 0) { throw "The setup-generated success record was rejected." }
    if (-not (Test-Path -LiteralPath $smokeResult -PathType Leaf)) { throw "Product launcher did not record GUI readiness: $smokeResult" }
    $smoke = Get-Content -LiteralPath $smokeResult -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $smoke.qmlLoaded -or $smoke.entrypoint -ne "SubtitleEditBayLauncher.exe") { throw "Installed GUI readiness contract failed." }
    if ($smoke.version -ne $ExpectedVersion -or $smoke.distribution -ne "installer") { throw "Installed GUI identity mismatch." }

    # Failure is also driven through the product EXE and real setup. setup.ps1,
    # not the test, must persist failed; normal launch must repair rather than
    # starting the GUI from the previously active runtime.
    Remove-Item -LiteralPath $hookStarted -Force
    $env:SUBTITLE_EDIT_BAY_HOOK_MODE = "fail"
    $repair = Start-Process -FilePath $launcher -ArgumentList "--setup" -WorkingDirectory $installDir -Wait -PassThru
    if ($repair.ExitCode -eq 0) {
        Get-Content -LiteralPath (Join-Path $installDir ".local\setup-status.json") -ErrorAction SilentlyContinue
        Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs\setup.log") -ErrorAction SilentlyContinue
        Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs\setup-error.log") -ErrorAction SilentlyContinue
        throw "The failing setup hook was accepted."
    }
    $failed = Get-Content -LiteralPath (Join-Path $installDir ".local\setup-status.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($failed.status -ne "failed" -or $failed.message -notmatch "hook") { throw "Setup did not persist the injected failure." }
    Remove-Item -LiteralPath $smokeResult -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $messageProbe -Force -ErrorAction SilentlyContinue
    $failedLaunch = Start-Process -FilePath $launcher -WorkingDirectory $installDir -Wait -PassThru
    if ($failedLaunch.ExitCode -eq 0) { throw "Normal launch accepted a failed setup." }
    if (Test-Path -LiteralPath $smokeResult) { throw "GUI started after setup failure." }
    if (-not (Test-Path -LiteralPath $messageProbe -PathType Leaf)) { throw "Normal launch did not report the setup failure." }
    $messages = @(Get-Content -LiteralPath $messageProbe -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
    $failureMessage = $messages | Select-Object -Last 1
    if (-not $failureMessage -or $failureMessage.title -notmatch "Subtitle Edit Bay" -or $failureMessage.message -notmatch [regex]::Escape((Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs\setup.log")) -or $failureMessage.message -notmatch "--setup") {
        throw "Normal launch setup failure did not include the reason, log, and retry guidance."
    }
} finally {
    foreach ($name in @(
        "SUBTITLE_EDIT_BAY_SETUP_TEST_HOOK", "SUBTITLE_EDIT_BAY_HOOK_COUNT",
        "SUBTITLE_EDIT_BAY_HOOK_STARTED", "SUBTITLE_EDIT_BAY_HOOK_GATE",
        "SUBTITLE_EDIT_BAY_HOOK_MODE", "SUBTITLE_EDIT_BAY_SUPPRESS_MESSAGES",
        "SUBTITLE_EDIT_BAY_MIGRATION_SOURCE",
        "SUBTITLE_EDIT_BAY_MESSAGE_PROBE", "SUBTITLE_EDIT_BAY_STARTUP_SMOKE_RESULT"
    )) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
}
