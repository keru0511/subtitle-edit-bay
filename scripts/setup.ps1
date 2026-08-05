$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

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

function Test-NvidiaGpu {
    $nvidiaSmi = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
    if (-not $nvidiaSmi) {
        return $false
    }
    & $nvidiaSmi.Source -L *> $null
    return $LASTEXITCODE -eq 0
}

Write-Host "Subtitle Edit Bay setup"
Write-Host "This can take a while because WhisperX and PyTorch are large."

$python = Find-Python310
if (-not $python) {
    Install-WithWinget -PackageId "Python.Python.3.10" -DisplayName "Python 3.10"
    $python = Find-Python310
}
if (-not $python) {
    throw "Python 3.10 was installed but could not be found. Close this window and run setup.bat again."
}
Write-Host "Python: $python"

$ffmpegDirectory = Find-FFmpegDirectory
if (-not $ffmpegDirectory) {
    Install-WithWinget -PackageId "Gyan.FFmpeg" -DisplayName "FFmpeg"
    $ffmpegDirectory = Find-FFmpegDirectory
}
if (-not $ffmpegDirectory) {
    throw "FFmpeg was installed but could not be found. Close this window and run setup.bat again."
}
$env:PATH = "$ffmpegDirectory;$env:PATH"
New-Item -ItemType Directory -Path ".local" -Force | Out-Null
[IO.File]::WriteAllText((Join-Path (Resolve-Path ".local") "ffmpeg_path.txt"), $ffmpegDirectory, (New-Object Text.UTF8Encoding($false)))
Write-Host "FFmpeg: $ffmpegDirectory"

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    Write-Host "Creating the private Python environment..."
    & $python -m venv ".venv"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create .venv."
    }
}

$venvPython = (Resolve-Path ".venv\Scripts\python.exe").Path
Write-Host "Updating pip..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip update failed." }

Write-Host "Installing Subtitle Edit Bay dependencies..."
& $venvPython -m pip install -r "requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "requirements.txt installation failed." }

$nvidiaGpuAvailable = Test-NvidiaGpu
$whisperXVersion = "3.8.6"
$torchVersion = "2.8.0"
$torchVisionVersion = "0.23.0"
$torchAudioVersion = "2.8.0"
$cudaTorchIndex = "https://download.pytorch.org/whl/cu128"

if ($nvidiaGpuAvailable) {
    $cudaAlreadyAvailable = & $venvPython -c "import importlib.util; has_torch = importlib.util.find_spec('torch') is not None; print('true' if has_torch and __import__('torch').cuda.is_available() else 'false')"
    $torchPackages = @(
        "torch==$torchVersion",
        "torchvision==$torchVisionVersion",
        "torchaudio==$torchAudioVersion"
    )
    $pipArguments = @(
        "-m",
        "pip",
        "install"
    ) + $torchPackages + @(
        "--index-url",
        $cudaTorchIndex
    )
    if ($cudaAlreadyAvailable.Trim() -ne "true") {
        Write-Host "CPU-only PyTorch detected. Replacing it with the CUDA build..."
        $pipArguments += @("--force-reinstall", "--no-deps")
    } else {
        Write-Host "CUDA-enabled PyTorch detected. Verifying pinned versions..."
    }

    & $venvPython @pipArguments
    if ($LASTEXITCODE -ne 0) { throw "CUDA-enabled PyTorch installation failed." }
}

Write-Host "Installing WhisperX $whisperXVersion..."
& $venvPython -m pip install "whisperx==$whisperXVersion"
if ($LASTEXITCODE -ne 0) { throw "WhisperX installation failed." }

if (-not (Test-Path -LiteralPath "assets\speaker_colors.json")) {
    Copy-Item -LiteralPath "assets\speaker_colors.example.json" -Destination "assets\speaker_colors.json"
}

$cudaAvailable = & $venvPython -c "import torch; print('true' if torch.cuda.is_available() else 'false')"
if ($LASTEXITCODE -ne 0) { throw "PyTorch verification failed." }
if ($nvidiaGpuAvailable -and $cudaAvailable.Trim() -ne "true") {
    throw "An NVIDIA GPU was detected, but CUDA-enabled PyTorch is unavailable. Re-run setup.bat after checking the NVIDIA driver and network connection."
}

if (-not (Test-Path -LiteralPath ".gui\runtime_config.json")) {
    New-Item -ItemType Directory -Path ".gui" -Force | Out-Null
    $config = Get-Content -LiteralPath "assets\runtime_config.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cudaAvailable.Trim() -ne "true") {
        $config.shared.device = "cpu"
        $config.shared.compute_type = "int8"
        $config.craig_pipeline.video_codec = "libx264"
    }
    $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath ".gui\runtime_config.json" -Encoding UTF8
}

& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Python dependency verification failed." }

& $venvPython -c "from src.runtime_dependencies import check_runtime_dependencies; status = check_runtime_dependencies(); assert status.ready, status.to_dict(); print(status.to_dict())"
if ($LASTEXITCODE -ne 0) { throw "Runtime dependency verification failed." }

if ($cudaAvailable.Trim() -eq "true") {
    Write-Host "CUDA: available"
} else {
    Write-Host "CUDA: unavailable. The first-run preset was configured for CPU and libx264."
}
Write-Host "Setup verification passed."