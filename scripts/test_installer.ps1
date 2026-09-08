param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$')][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$InstallDirectory
)

$ErrorActionPreference = "Stop"
$installer = [IO.Path]::GetFullPath($InstallerPath)
$installDir = [IO.Path]::GetFullPath($InstallDirectory)
$testRoot = [IO.Path]::GetDirectoryName($installDir)
$logPath = Join-Path $testRoot "subtitle-edit-bay-install.log"
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw "Installer is missing: $installer" }

$install = Start-Process -FilePath $installer -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$installDir", "/LOG=$logPath") -Wait -PassThru
if ($install.ExitCode -ne 0) {
    Get-Content -LiteralPath $logPath -ErrorAction SilentlyContinue
    throw "Installer exited with code $($install.ExitCode)."
}
foreach ($path in @(
    "SubtitleEditBayLauncher.exe", "src\gui.py", "src\ui\Main.qml", "scripts\launch.ps1",
    "scripts\setup.ps1", "scripts\setup_state.ps1", "scripts\runtime_activation.ps1",
    "scripts\runtime_contract.py", "runtime\runtime-contract.json",
    "runtime\requirements-windows-cpu.lock", "runtime\requirements-windows-cu128.lock", "VERSION"
)) {
    $candidate = Join-Path $installDir $path
    if (-not (Test-Path -LiteralPath $candidate)) { throw "Installed file is missing: $candidate" }
}
$expectedInstalledVersion = $ExpectedVersion.Substring(1)
$installedVersion = (Get-Content -LiteralPath (Join-Path $installDir "VERSION") -Raw).Trim()
if ($installedVersion -ne $expectedInstalledVersion) { throw "Installed VERSION mismatch: expected=$expectedInstalledVersion actual=$installedVersion" }

$launcher = Join-Path $installDir "SubtitleEditBayLauncher.exe"
$providerPath = Join-Path $testRoot "installer-e2e-dependency-provider.ps1"
$providerCount = Join-Path $testRoot "installer-e2e-provider-count.txt"
$providerStarted = Join-Path $testRoot "installer-e2e-provider-started.txt"
$providerGate = Join-Path $testRoot "installer-e2e-provider-gate.txt"
$smokeResult = Join-Path $testRoot "installed-gui-smoke.json"
$provider = @'
param(
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [Parameter(Mandatory = $true)][string]$CandidateManifestPath,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$RuntimeProfile
)
Add-Content -LiteralPath $env:SUBTITLE_EDIT_BAY_PROVIDER_COUNT -Value "run" -Encoding ascii
[IO.File]::WriteAllText($env:SUBTITLE_EDIT_BAY_PROVIDER_STARTED, "started")
if ($env:SUBTITLE_EDIT_BAY_PROVIDER_MODE -eq "wait") {
    $deadline = [DateTime]::UtcNow.AddMinutes(2)
    while (-not (Test-Path -LiteralPath $env:SUBTITLE_EDIT_BAY_PROVIDER_GATE)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw "Timed out waiting for the E2E provider gate." }
        Start-Sleep -Milliseconds 100
    }
}
if ($env:SUBTITLE_EDIT_BAY_PROVIDER_MODE -eq "fail") {
    Write-Error "Synthetic dependency provider failure"
    exit 17
}
python -m venv $RuntimeDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$runtimePython = Join-Path $RuntimeDirectory "Scripts\python.exe"
$contract = Get-Content -LiteralPath (Join-Path $ProjectRoot "runtime\runtime-contract.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$profile = $contract.profiles.$RuntimeProfile
& $runtimePython -m pip install "pip==$($contract.python.pip_version)"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$pipArguments = @("-m", "pip", "install", "--require-hashes", "--index-url", [string]$profile.index_url)
if ($profile.extra_index_url) { $pipArguments += @("--extra-index-url", [string]$profile.extra_index_url) }
$pipArguments += @("-r", (Join-Path $ProjectRoot ([string]$profile.lock_file)))
& $runtimePython @pipArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $runtimePython (Join-Path $ProjectRoot "scripts\runtime_contract.py") verify-runtime --root $ProjectRoot --profile $RuntimeProfile --manifest-output $CandidateManifestPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
'@
[IO.File]::WriteAllText($providerPath, $provider, (New-Object Text.UTF8Encoding($false)))

$env:SUBTITLE_EDIT_BAY_SETUP_PROVIDER = $providerPath
$env:SUBTITLE_EDIT_BAY_PROVIDER_COUNT = $providerCount
$env:SUBTITLE_EDIT_BAY_PROVIDER_STARTED = $providerStarted
$env:SUBTITLE_EDIT_BAY_PROVIDER_GATE = $providerGate
$env:SUBTITLE_EDIT_BAY_PROVIDER_MODE = "wait"
$env:SUBTITLE_EDIT_BAY_SUPPRESS_MESSAGES = "1"
$env:SUBTITLE_EDIT_BAY_STARTUP_SMOKE_RESULT = $smokeResult
try {
    # Product EXE -> launch.ps1 -> real setup.ps1. Keep the provider paused,
    # then prove normal launch and an explicit repair both attach to that one
    # setup instead of starting another dependency build.
    $first = Start-Process -FilePath $launcher -ArgumentList "--setup" -WorkingDirectory $installDir -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath $providerStarted) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (-not (Test-Path -LiteralPath $providerStarted)) { throw "The real setup path did not reach the dependency provider." }
    $runningStatus = Get-Content -LiteralPath (Join-Path $installDir ".local\setup-status.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($runningStatus.status -ne "running" -or -not $runningStatus.process_id) { throw "The real setup did not publish its running state." }
    $runningProbe = Start-Process -FilePath $launcher -ArgumentList "--probe-setup-running" -WorkingDirectory $installDir -Wait -PassThru
    if ($runningProbe.ExitCode -ne 0) { throw "The process-aware setup lock was not observable." }
    $second = Start-Process -FilePath $launcher -ArgumentList "--setup" -WorkingDirectory $installDir -PassThru
    $normal = Start-Process -FilePath $launcher -WorkingDirectory $installDir -PassThru
    Start-Sleep -Seconds 2
    $runsBeforeRelease = @(Get-Content -LiteralPath $providerCount).Count
    if ($runsBeforeRelease -ne 1) { throw "Concurrent product launches started $runsBeforeRelease dependency builds." }
    [IO.File]::WriteAllText($providerGate, "continue")
    foreach ($process in @($first, $second, $normal)) {
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Concurrent product path exited with code $($process.ExitCode)." }
    }
    $providerRuns = @(Get-Content -LiteralPath $providerCount).Count
    if ($providerRuns -ne 1) { throw "Expected one dependency build, found $providerRuns." }
    $stoppedProbe = Start-Process -FilePath $launcher -ArgumentList "--probe-setup-running" -WorkingDirectory $installDir -Wait -PassThru
    if ($stoppedProbe.ExitCode -ne 3) { throw "The setup lock was not released after completion." }

    $statusPath = Join-Path $installDir ".local\setup-status.json"
    $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($status.status -ne "success" -or $status.app_version -ne $expectedInstalledVersion -or -not $status.process_id) {
        throw "The real setup did not publish a valid success record: $($status | ConvertTo-Json -Compress)"
    }
    $runtimeManifest = Get-Content -LiteralPath (Join-Path $installDir ".local\runtime-manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $runtimeDirectory = [IO.Path]::GetFullPath((Join-Path $installDir ([string]$runtimeManifest.runtime_directory)))
    $venvPip = Join-Path $runtimeDirectory "Scripts\pip.exe"
    & $venvPip --version
    if ($LASTEXITCODE -ne 0) { throw "The activated runtime pip command is broken." }

    $readyProbe = Start-Process -FilePath $launcher -ArgumentList "--probe-setup" -WorkingDirectory $installDir -Wait -PassThru
    if ($readyProbe.ExitCode -ne 0) { throw "The setup-generated success record was rejected." }
    if (-not (Test-Path -LiteralPath $smokeResult -PathType Leaf)) { throw "Product launcher did not record GUI readiness: $smokeResult" }
    $smoke = Get-Content -LiteralPath $smokeResult -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $smoke.qmlLoaded -or $smoke.entrypoint -ne "SubtitleEditBayLauncher.exe") { throw "Installed GUI readiness contract failed." }
    if ($smoke.version -ne $ExpectedVersion -or $smoke.distribution -ne "installer") { throw "Installed GUI identity mismatch." }

    # Failure is also driven through the product EXE and real setup. setup.ps1,
    # not the test, must persist failed; normal launch must repair rather than
    # starting the GUI from the previously active runtime.
    Remove-Item -LiteralPath $providerStarted -Force
    $env:SUBTITLE_EDIT_BAY_PROVIDER_MODE = "fail"
    $repair = Start-Process -FilePath $launcher -ArgumentList "--setup" -WorkingDirectory $installDir -Wait -PassThru
    if ($repair.ExitCode -eq 0) {
        Get-Content -LiteralPath (Join-Path $installDir ".local\setup-status.json") -ErrorAction SilentlyContinue
        Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs\setup.log") -ErrorAction SilentlyContinue
        Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs\setup-error.log") -ErrorAction SilentlyContinue
        throw "The failing provider was accepted."
    }
    $failed = Get-Content -LiteralPath (Join-Path $installDir ".local\setup-status.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($failed.status -ne "failed" -or $failed.message -notmatch "provider") { throw "Setup did not persist the provider failure." }
    Remove-Item -LiteralPath $smokeResult -Force -ErrorAction SilentlyContinue
    $failedLaunch = Start-Process -FilePath $launcher -WorkingDirectory $installDir -Wait -PassThru
    if ($failedLaunch.ExitCode -eq 0) { throw "Normal launch accepted a failed setup." }
    if (Test-Path -LiteralPath $smokeResult) { throw "GUI started after setup failure." }
} finally {
    foreach ($name in @(
        "SUBTITLE_EDIT_BAY_SETUP_PROVIDER", "SUBTITLE_EDIT_BAY_PROVIDER_COUNT",
        "SUBTITLE_EDIT_BAY_PROVIDER_STARTED", "SUBTITLE_EDIT_BAY_PROVIDER_GATE",
        "SUBTITLE_EDIT_BAY_PROVIDER_MODE", "SUBTITLE_EDIT_BAY_SUPPRESS_MESSAGES",
        "SUBTITLE_EDIT_BAY_STARTUP_SMOKE_RESULT"
    )) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
}
