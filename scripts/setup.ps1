param(
    [switch]$ProbeNvidiaOnly,
    [switch]$ProbeNvidiaStatusOnly,
    [switch]$ProbeCpuInstallArgumentsOnly,
    [switch]$ProbePendingMigrationOnly,
    [string]$NvidiaSmiSearchRoot = "",
    [string]$NvidiaSmiOverride = "",
    [string]$MigrationSource = $env:SUBTITLE_EDIT_BAY_MIGRATION_SOURCE,
    [switch]$SkipRuntimeConfig = ($env:SUBTITLE_EDIT_BAY_SKIP_RUNTIME_CONFIG -eq "1"),
    [switch]$SkipSpeakerColors = ($env:SUBTITLE_EDIT_BAY_SKIP_SPEAKER_COLORS -eq "1"),
    [switch]$SkipWorkspaceReference = ($env:SUBTITLE_EDIT_BAY_SKIP_WORKSPACE_REFERENCE -eq "1"),
    [string]$PendingMigrationRequestPath = "",
    [string]$SetupTestHook = $env:SUBTITLE_EDIT_BAY_SETUP_TEST_HOOK,
    [switch]$KeepPreviousRuntime
)

# Windows PowerShell 5.1 turns text written to stderr by native programs into
# error records. Native exit codes are checked explicitly throughout this
# script, so let those commands finish while keeping PowerShell cmdlets strict.
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$projectRoot = (Get-Location).Path
. (Join-Path $PSScriptRoot "runtime_activation.ps1")
. (Join-Path $PSScriptRoot "setup_state.ps1")
. (Join-Path $PSScriptRoot "windows_path_identity.ps1")

function Find-Python310 {
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($launcher) {
        $resolved = & $launcher.Source -3.10 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolved) {
            return ($resolved | Select-Object -Last 1).Trim()
        }
    }

    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($python) {
        $version = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -eq "3.10") {
            return $python.Source
        }
    }

    $knownPath = Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe"
    if (Test-Path -LiteralPath $knownPath) {
        return $knownPath
    }
    return $null
}

function Find-FFmpegDirectory {
    $ffmpeg = Get-Command "ffmpeg.exe" -ErrorAction SilentlyContinue
    $ffprobe = Get-Command "ffprobe.exe" -ErrorAction SilentlyContinue
    if ($ffmpeg -and $ffprobe) {
        return (Split-Path -Parent $ffmpeg.Source)
    }

    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"),
        (Join-Path $env:ProgramFiles "WinGet\Links"),
        (Join-Path $env:ProgramFiles "WinGet\Packages")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

    foreach ($root in $roots) {
        $candidate = Get-ChildItem -LiteralPath $root -Filter "ffmpeg.exe" -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($candidate -and (Test-Path -LiteralPath (Join-Path $candidate.DirectoryName "ffprobe.exe"))) {
            return $candidate.DirectoryName
        }
    }
    return $null
}

function Install-WithWinget {
    param(
        [Parameter(Mandatory = $true)][string]$PackageId,
        [Parameter(Mandatory = $true)][string]$DisplayName
    )

    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "$DisplayName is missing and winget is unavailable. Install App Installer from Microsoft Store, then run setup.bat again."
    }

    Write-Host "Installing $DisplayName with winget..."
    & $winget.Source install --exact --id $PackageId --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install $DisplayName (exit code $LASTEXITCODE)."
    }
}

function Find-NvidiaSmi {
    param([string]$WindowsRoot = "")

    $candidates = @()
    $nvidiaSmi = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
    if ($nvidiaSmi) {
        $candidates += $nvidiaSmi.Source
    }
    $resolvedWindowsRoot = if ($WindowsRoot) { $WindowsRoot } else { $env:SystemRoot }
    if ($resolvedWindowsRoot) {
        $candidates += Join-Path $resolvedWindowsRoot "Sysnative\nvidia-smi.exe"
        $candidates += Join-Path $resolvedWindowsRoot "System32\nvidia-smi.exe"
    }
    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles "NVIDIA Corporation\NVSMI\nvidia-smi.exe"
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Get-Item -LiteralPath $candidate).FullName
        }
    }
    return $null
}

function Get-NvidiaGpuProbe {
    param([string]$NvidiaSmiPath)

    if (-not $NvidiaSmiPath) {
        return [PSCustomObject]@{
            State = "not_found"
            ExitCode = $null
            Output = ""
        }
    }

    try {
        $outputLines = @(& $NvidiaSmiPath -L 2>&1)
        $exitCode = if ($null -eq $LASTEXITCODE) { -1 } else { [int]$LASTEXITCODE }
        $outputText = ($outputLines | ForEach-Object { "$_" }) -join [Environment]::NewLine
    } catch {
        return [PSCustomObject]@{
            State = "execution_failed"
            ExitCode = -1
            Output = "$_"
        }
    }

    return [PSCustomObject]@{
        State = if ($exitCode -eq 0) { "available" } else { "execution_failed" }
        ExitCode = $exitCode
        Output = $outputText
    }
}

function Get-RuntimePipArguments {
    param(
        [Parameter(Mandatory = $true)]$ProfileContract,
        [Parameter(Mandatory = $true)][string]$RuntimeLock
    )
    $arguments = @("-m", "pip", "install", "--require-hashes", "--index-url", [string]$ProfileContract.index_url)
    $extraIndexProperty = $ProfileContract.PSObject.Properties["extra_index_url"]
    if ($extraIndexProperty -and $extraIndexProperty.Value) {
        $arguments += @("--extra-index-url", [string]$extraIndexProperty.Value)
    }
    $arguments += @("-r", $RuntimeLock)
    return $arguments
}

if ($ProbeNvidiaOnly) {
    $probePath = Find-NvidiaSmi -WindowsRoot $NvidiaSmiSearchRoot
    if ($probePath) {
        Write-Output $probePath
        exit 0
    }
    exit 1
}

if ($ProbeNvidiaStatusOnly) {
    $probePath = if ($NvidiaSmiOverride) {
        $NvidiaSmiOverride
    } else {
        Find-NvidiaSmi -WindowsRoot $NvidiaSmiSearchRoot
    }
    $probeResult = Get-NvidiaGpuProbe -NvidiaSmiPath $probePath
    Write-Output ($probeResult | ConvertTo-Json -Compress)
    if ($probeResult.State -eq "execution_failed") {
        exit 2
    }
    exit 0
}

if ($ProbeCpuInstallArgumentsOnly) {
    $probeContract = Get-Content -LiteralPath "runtime\runtime-contract.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    $probeProfile = $probeContract.profiles.cpu
    Write-Output (Get-RuntimePipArguments -ProfileContract $probeProfile -RuntimeLock ([string]$probeProfile.lock_file) | ConvertTo-Json -Compress)
    exit 0
}

$pendingMigrationPath = if ($PendingMigrationRequestPath) {
    [IO.Path]::GetFullPath($PendingMigrationRequestPath)
} else {
    Join-Path $projectRoot ".local\migration\pending-request.json"
}
$pendingMigration = $null
if (Test-Path -LiteralPath $pendingMigrationPath -PathType Leaf) {
    try {
        $pendingMigration = Get-Content -LiteralPath $pendingMigrationPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($pendingMigration.schema_version -ne 1 -or -not $pendingMigration.source) {
            throw "unsupported or incomplete pending migration request"
        }
    } catch {
        throw "The pending migration request is invalid. Cancel it or reinstall before setup: $_"
    }
}
if (-not $MigrationSource -and $pendingMigration) {
    $MigrationSource = [string]$pendingMigration.source
    $SkipRuntimeConfig = [bool]$pendingMigration.skip_runtime_config
    $SkipSpeakerColors = [bool]$pendingMigration.skip_speaker_colors
    $SkipWorkspaceReference = [bool]$pendingMigration.skip_workspace_reference
}

$clearPendingMigrationOnSuccess = $false
if ($MigrationSource) {
    # This guard deliberately runs before the mutex status, dependency install,
    # runtime generation or compatibility junction can change the destination.
    try {
        $resolvedMigrationSource = Resolve-FinalDirectoryPath -Path $MigrationSource
        $resolvedDestination = Resolve-FinalDirectoryPath -Path $projectRoot
    } catch {
        throw "The migration source path is invalid: $_"
    }
    if ($resolvedMigrationSource.TrimEnd('\', '/') -eq $resolvedDestination.TrimEnd('\', '/')) {
        throw "The migration source and installation destination must be different. No setup changes were made."
    }
    if (-not (Test-Path -LiteralPath $resolvedMigrationSource -PathType Container) -or
        -not (Test-Path -LiteralPath (Join-Path $resolvedMigrationSource "setup.bat") -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $resolvedMigrationSource "start.bat") -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $resolvedMigrationSource "src") -PathType Container)) {
        throw "The migration source is not a BAT/ZIP Subtitle Edit Bay workspace. No setup changes were made."
    }
    $MigrationSource = $resolvedMigrationSource
    if ($pendingMigration) {
        try {
            $pendingSource = Resolve-FinalDirectoryPath -Path ([string]$pendingMigration.source)
            $clearPendingMigrationOnSuccess =
                $pendingSource.TrimEnd('\', '/') -eq $resolvedMigrationSource.TrimEnd('\', '/')
        } catch {
            $clearPendingMigrationOnSuccess = $false
        }
    }
}
if ($ProbePendingMigrationOnly) {
    Write-Output (@{
        source = $MigrationSource
        skip_runtime_config = [bool]$SkipRuntimeConfig
        skip_speaker_colors = [bool]$SkipSpeakerColors
        skip_workspace_reference = [bool]$SkipWorkspaceReference
        pending_request_preserved = (Test-Path -LiteralPath $pendingMigrationPath -PathType Leaf)
    } | ConvertTo-Json -Compress)
    exit 0
}

$setupLease = Enter-SetupMutex -ProjectRoot $projectRoot
if (-not $setupLease.Acquired) {
    $setupLease.Mutex.Dispose()
    Write-Host "Setup is already running for this installation."
    exit 32
}
$runtimeActivationCommitted = $false
$uncommittedRuntimePaths = @()
Write-SetupStatus -ProjectRoot $projectRoot -Status "running" -Stage "Starting setup"
trap {
    $setupError = $_
    if (-not $runtimeActivationCommitted) {
        foreach ($uncommittedPath in $uncommittedRuntimePaths) {
            if (-not (Test-Path -LiteralPath $uncommittedPath)) { continue }
            try { Remove-Item -LiteralPath $uncommittedPath -Recurse -Force }
            catch { Write-Warning ("Could not remove uncommitted runtime data at " + $uncommittedPath + ": " + $_) }
        }
    }
    Write-SetupStatus -ProjectRoot $projectRoot -Status "failed" -Stage "Setup failed" -Message $setupError.Exception.Message
    Exit-SetupMutex -Lease $setupLease
    exit 1
}

Write-Host "Subtitle Edit Bay setup"
Write-Host "This can take a while because WhisperX and PyTorch are large."
Write-SetupStatus -ProjectRoot $projectRoot -Status "running" -Stage "Checking system requirements"

$runtimeContractPath = "runtime\runtime-contract.json"
$runtimeContract = Get-Content -LiteralPath $runtimeContractPath -Raw -Encoding UTF8 | ConvertFrom-Json
$python = Find-Python310
if (-not $python) {
    Install-WithWinget -PackageId $runtimeContract.python.winget_package -DisplayName "Python 3.10"
    $python = Find-Python310
}
if (-not $python) {
    throw "Python 3.10 was installed but could not be found. Close this window and run setup.bat again."
}
Write-Host "Python: $python"

& $python "scripts\runtime_contract.py" validate --root "."
if ($LASTEXITCODE -ne 0) { throw "The bundled runtime contract or lock file is invalid." }
& $python "scripts\runtime_contract.py" verify-python --root "."
if ($LASTEXITCODE -ne 0) { throw "The detected Python does not satisfy the release runtime contract." }
$ffmpegDirectory = Find-FFmpegDirectory
if (-not $ffmpegDirectory) {
    Install-WithWinget -PackageId $runtimeContract.ffmpeg.winget_package -DisplayName "FFmpeg"
    $ffmpegDirectory = Find-FFmpegDirectory
}
if (-not $ffmpegDirectory) {
    throw "FFmpeg was installed but could not be found. Close this window and run setup.bat again."
}
$env:PATH = "$ffmpegDirectory;$env:PATH"
New-Item -ItemType Directory -Path ".local" -Force | Out-Null
[IO.File]::WriteAllText((Join-Path (Resolve-Path ".local") "ffmpeg_path.txt"), $ffmpegDirectory, (New-Object Text.UTF8Encoding($false)))
Write-Host "FFmpeg: $ffmpegDirectory"
& $python "scripts\runtime_contract.py" verify-tools --root "."
if ($LASTEXITCODE -ne 0) { throw "FFmpeg or ffprobe does not satisfy the release runtime contract." }

$shellArchitectureBits = [IntPtr]::Size * 8
$nvidiaSmiPath = Find-NvidiaSmi
$nvidiaGpuProbe = Get-NvidiaGpuProbe -NvidiaSmiPath $nvidiaSmiPath
$nvidiaGpuAvailable = $nvidiaGpuProbe.State -eq "available"
Write-Host "PowerShell architecture: $shellArchitectureBits-bit"
if ($nvidiaSmiPath) {
    Write-Host "NVIDIA SMI: $nvidiaSmiPath"
}
if ($nvidiaGpuProbe.State -eq "execution_failed") {
    Write-Host "NVIDIA SMI probe failed (exit code $($nvidiaGpuProbe.ExitCode))."
    if ($nvidiaGpuProbe.Output) {
        Write-Host $nvidiaGpuProbe.Output
    }
    throw "NVIDIA tools were found, but the GPU driver check failed. Update or reinstall the NVIDIA driver, restart Windows, then run setup.bat again."
}
if ($nvidiaGpuAvailable) {
    $gpuNameLines = @(& $nvidiaSmiPath --query-gpu=name --format=csv,noheader 2>&1)
    $gpuNameExitCode = $LASTEXITCODE
    if ($gpuNameExitCode -ne 0) {
        Write-Host "NVIDIA GPU name query failed (exit code $gpuNameExitCode)."
        if ($gpuNameLines) {
            Write-Host (($gpuNameLines | ForEach-Object { "$_" }) -join [Environment]::NewLine)
        }
        throw "NVIDIA tools were found, but the GPU driver query failed. Update or reinstall the NVIDIA driver, restart Windows, then run setup.bat again."
    }
    if ($gpuNameLines) {
        Write-Host "NVIDIA GPU: $(($gpuNameLines | ForEach-Object { "$($_)".Trim() }) -join ', ')"
    }
} else {
    Write-Host "NVIDIA GPU: not found"
}

$runtimeProfile = if ($nvidiaGpuAvailable) { "cu128" } else { "cpu" }
$profileContract = $runtimeContract.profiles.$runtimeProfile
$runtimeLock = [string]$profileContract.lock_file
$runtimeRoot = ".local\runtimes"
$runtimeGeneration = "runtime-$runtimeProfile-$([Guid]::NewGuid().ToString('N'))"
$runtimeVenv = Join-Path $runtimeRoot $runtimeGeneration
$activeManifest = ".local\runtime-manifest.json"
$candidateManifest = ".local\runtime-manifest.$runtimeGeneration.json"
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$runtimeVenvFull = [IO.Path]::GetFullPath((Join-Path $projectRoot $runtimeVenv))
$activeManifestFull = [IO.Path]::GetFullPath((Join-Path $projectRoot $activeManifest))
$candidateManifestFull = [IO.Path]::GetFullPath((Join-Path $projectRoot $candidateManifest))
$uncommittedRuntimePaths = @($candidateManifest, $runtimeVenv)

$previousRuntime = $null
if (Test-Path -LiteralPath $activeManifest -PathType Leaf) {
    try {
        $previousRecord = Get-Content -LiteralPath $activeManifest -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($previousRecord.runtime_directory) {
            $candidatePreviousRuntime = [IO.Path]::GetFullPath((Join-Path $projectRoot ([string]$previousRecord.runtime_directory)))
            $runtimeRootFull = [IO.Path]::GetFullPath((Join-Path $projectRoot $runtimeRoot))
            if ($candidatePreviousRuntime.StartsWith($runtimeRootFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                $previousRuntime = $candidatePreviousRuntime
            }
        }
    } catch {
        throw "The active runtime manifest is invalid. Repair it or remove .local\runtime-manifest.json before setup: $_"
    }
}

Write-Host "Building the $runtimeProfile runtime from $runtimeLock..."
Write-SetupStatus -ProjectRoot $projectRoot -Status "running" -Stage "Building the pinned runtime"
if ($SetupTestHook) {
    $hookPath = [IO.Path]::GetFullPath($SetupTestHook)
    if (-not (Test-Path -LiteralPath $hookPath -PathType Leaf)) { throw "The setup test hook is missing: $hookPath" }
    $hookPowerShell = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    & $hookPowerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $hookPath -Phase "BeforeRuntimeBuild" -RuntimeDirectory $runtimeVenvFull -ProjectRoot $projectRoot -RuntimeProfile $runtimeProfile
    if ($LASTEXITCODE -ne 0) { throw "Setup test hook failed with exit code $LASTEXITCODE." }
}
& $python -m venv $runtimeVenv
if ($LASTEXITCODE -ne 0) { throw "Could not create the new Python runtime generation." }
$runtimePython = (Resolve-Path "$runtimeVenv\Scripts\python.exe").Path
& $runtimePython -m pip install "pip==$($runtimeContract.python.pip_version)"
if ($LASTEXITCODE -ne 0) { throw "Pinned pip installation failed." }
$pipArguments = Get-RuntimePipArguments -ProfileContract $profileContract -RuntimeLock $runtimeLock
& $runtimePython @pipArguments
if ($LASTEXITCODE -ne 0) { throw "Locked runtime installation failed. The existing runtime was not changed." }
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) { throw "The runtime Python executable was not created." }
& $runtimePython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Locked runtime dependency verification failed. The existing runtime was not changed." }
& $runtimePython "scripts\runtime_contract.py" verify-runtime --root "." --profile $runtimeProfile --manifest-output $candidateManifest
if ($LASTEXITCODE -ne 0) { throw "Runtime contract verification failed. The existing runtime was not changed." }

$manifestRecord = Get-Content -LiteralPath $candidateManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$manifestRecord | Add-Member -NotePropertyName runtime_directory -NotePropertyValue $runtimeVenv
[IO.File]::WriteAllText(
    $candidateManifestFull,
    ($manifestRecord | ConvertTo-Json -Depth 10) + [Environment]::NewLine,
    (New-Object Text.UTF8Encoding($false))
)

$torchRuntimeJson = & $runtimePython -c "import json, torch; available = torch.cuda.is_available(); print(json.dumps({'version': torch.__version__, 'cuda_runtime': torch.version.cuda, 'cuda_available': available, 'device_name': torch.cuda.get_device_name(0) if available else ''}))"
if ($LASTEXITCODE -ne 0 -or -not $torchRuntimeJson) { throw "PyTorch verification failed." }
$torchRuntime = ($torchRuntimeJson | Select-Object -Last 1) | ConvertFrom-Json
$cudaAvailable = [bool]$torchRuntime.cuda_available
$cudaRuntime = if ($torchRuntime.cuda_runtime) { $torchRuntime.cuda_runtime } else { "none" }
Write-Host "PyTorch: $($torchRuntime.version)"
Write-Host "PyTorch CUDA runtime: $cudaRuntime"
Write-Host "PyTorch CUDA available: $($cudaAvailable.ToString().ToLowerInvariant())"
if ($cudaAvailable -and $torchRuntime.device_name) {
    Write-Host "PyTorch CUDA device: $($torchRuntime.device_name)"
}
if ($nvidiaGpuAvailable -and -not $cudaAvailable) {
    throw "An NVIDIA GPU was detected, but CUDA-enabled PyTorch is unavailable. Re-run setup.bat after checking the NVIDIA driver and network connection."
}

$cleanupDirectories = @()
if (-not $KeepPreviousRuntime -and $previousRuntime -and $previousRuntime -ne $runtimeVenvFull) {
    $cleanupDirectories += $previousRuntime
}
$verifyActivatedRuntime = {
    & $runtimePython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Python dependency verification failed after runtime activation." }
    & $runtimePython -c "from src.runtime_dependencies import check_runtime_dependencies; status = check_runtime_dependencies(); assert status.ready, status.to_dict(); print(status.to_dict())"
    if ($LASTEXITCODE -ne 0) { throw "Runtime dependency verification failed after runtime activation." }
}.GetNewClosure()
Set-ActiveRuntimeGeneration `
    -NewRuntimeDirectory $runtimeVenvFull `
    -CandidateManifestPath $candidateManifestFull `
    -ActiveManifestPath $activeManifestFull `
    -VerifyScript $verifyActivatedRuntime `
    -CleanupDirectories $cleanupDirectories
$runtimeActivationCommitted = $true
$venvPython = $runtimePython

# Keep the documented .venv command path as a compatibility junction. It is
# not the activation mechanism; launch.ps1 resolves the committed manifest.
try {
    if (Test-Path -LiteralPath ".venv") {
        $legacyRuntime = Get-Item -LiteralPath ".venv" -Force
        if ($legacyRuntime.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Remove-Item -LiteralPath ".venv" -Force
        } else {
            Remove-Item -LiteralPath ".venv" -Recurse -Force
        }
    }
    New-Item -ItemType Junction -Path ".venv" -Target $runtimeVenvFull | Out-Null
} catch {
    Write-Warning "The active runtime is valid, but the optional .venv compatibility junction could not be refreshed: $_"
}

if ($MigrationSource) {
    Write-Host "Migrating settings from the BAT/ZIP workspace: $MigrationSource"
    $migrationArguments = @(
        "-m",
        "src.installer_migration",
        "--source",
        $MigrationSource,
        "--destination",
        (Get-Location).Path
    )
    if ($SkipRuntimeConfig) { $migrationArguments += "--skip-runtime-config" }
    if ($SkipSpeakerColors) { $migrationArguments += "--skip-speaker-colors" }
    if ($SkipWorkspaceReference) { $migrationArguments += "--skip-workspace-reference" }
    if ($cudaAvailable) { $migrationArguments += "--cuda" }
    $nvencAvailableText = & $venvPython -c "from src.runtime_dependencies import check_runtime_dependencies; print('true' if check_runtime_dependencies(probe_nvenc=True).nvenc else 'false')"
    if ($LASTEXITCODE -ne 0) { throw "Runtime capability verification for migration failed." }
    if ($nvencAvailableText.Trim() -eq "true") { $migrationArguments += "--nvenc" }
    & $venvPython @migrationArguments
    if ($LASTEXITCODE -ne 0) {
        throw "BAT/ZIP workspace migration failed. The old workspace was not modified."
    }
    if ($clearPendingMigrationOnSuccess -and (Test-Path -LiteralPath $pendingMigrationPath -PathType Leaf)) {
        Remove-Item -LiteralPath $pendingMigrationPath -Force
    }
}

$configPath = ".gui\runtime_config.json"
$configChanged = $false
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
    New-Item -ItemType Directory -Path ".gui" -Force | Out-Null
    $config = Get-Content -LiteralPath "assets\runtime_config.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    $configChanged = $true
}
if (-not $cudaAvailable) {
    if ($config.shared.device -eq "cuda") {
        $config.shared.device = "cpu"
        $config.shared.compute_type = "int8"
        $configChanged = $true
        Write-Host "Runtime config: changed unavailable CUDA selection to cpu/int8."
    }
    if (-not $nvidiaGpuAvailable -and $config.craig_pipeline.video_codec -eq "h264_nvenc") {
        $config.craig_pipeline.video_codec = "libx264"
        $configChanged = $true
    }
}
if ($configChanged) {
    $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding UTF8
}

if (-not (Test-Path -LiteralPath "assets\speaker_colors.json")) {
    Copy-Item -LiteralPath "assets\speaker_colors.example.json" -Destination "assets\speaker_colors.json"
}

if ($cudaAvailable) {
    Write-Host "CUDA: available"
} else {
    Write-Host "CUDA: unavailable. The first-run preset was configured for CPU and libx264."
}
Write-Host "Setup verification passed."
Write-SetupStatus -ProjectRoot $projectRoot -Status "success" -Stage "Setup completed" -Details @{
    python = $venvPython
    cuda_available = $cudaAvailable
    cuda_runtime = $cudaRuntime
    device_name = [string]$torchRuntime.device_name
    ffmpeg_directory = $ffmpegDirectory
}
Exit-SetupMutex -Lease $setupLease
