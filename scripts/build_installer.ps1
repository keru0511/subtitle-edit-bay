param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputPath,

    [string]$IsccPath,

    [switch]$RequireSignature,

    [string]$ExpectedSignerSubject,

    [string]$TimestampServer = "http://timestamp.digicert.com",

    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

$projectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$installerScript = Join-Path $projectRoot "installer\SubtitleEditBay.iss"

if ($RequireSignature -and [string]::IsNullOrWhiteSpace($ExpectedSignerSubject)) {
    throw "ExpectedSignerSubject is required when formal Authenticode signing is enabled."
}

function Find-InnoSetupCompiler {
    param([string]$ExplicitPath)

    $candidates = @()
    if ($ExplicitPath) {
        $candidates += $ExplicitPath
    }
    if ($env:INNO_SETUP_COMPILER) {
        $candidates += $env:INNO_SETUP_COMPILER
    }

    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($command) {
        $candidates += $command.Source
    }

    if (${env:ProgramFiles(x86)}) {
        $candidates += Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    }
    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"
    }
    if ($env:ProgramData) {
        $candidates += Join-Path $env:ProgramData "chocolatey\bin\ISCC.exe"
    }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Inno Setup 6 compiler (ISCC.exe) was not found. Install Inno Setup 6 or set INNO_SETUP_COMPILER."
}

if (-not (Test-Path -LiteralPath $installerScript -PathType Leaf)) {
    throw "Installer definition is missing: $installerScript"
}

$resolvedOutputPath = if ([IO.Path]::IsPathRooted($OutputPath)) {
    [IO.Path]::GetFullPath($OutputPath)
}
else {
    [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
}

if ([IO.Path]::GetExtension($resolvedOutputPath) -ne ".exe") {
    throw "OutputPath must end with .exe: $resolvedOutputPath"
}

$outputDirectory = Split-Path -Parent $resolvedOutputPath
$outputBaseFilename = [IO.Path]::GetFileNameWithoutExtension($resolvedOutputPath)
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$launcherBuildScript = Join-Path $projectRoot "scripts\build_launcher.ps1"
if (-not (Test-Path -LiteralPath $launcherBuildScript -PathType Leaf)) {
    throw "Launcher build script is missing: $launcherBuildScript"
}
$launcherPath = Join-Path $projectRoot "dist\SubtitleEditBayLauncher.exe"
& pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcherBuildScript `
    -OutputPath $launcherPath `
    -Version $Version
if ($LASTEXITCODE -ne 0) {
    throw "Launcher build failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw "Launcher build did not produce the required executable: $launcherPath"
}

if ($RequireSignature) {
    $signingScript = Join-Path $projectRoot "scripts\sign_windows_artifacts.ps1"
    if (-not (Test-Path -LiteralPath $signingScript -PathType Leaf)) {
        throw "Windows signing script is missing: $signingScript"
    }
    & pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File $signingScript `
        -Path $launcherPath `
        -ExpectedSignerSubject $ExpectedSignerSubject `
        -TimestampServer $TimestampServer
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher Authenticode signing failed with exit code $LASTEXITCODE."
    }
}

$versionCore = ($Version -split '[-+]')[0]
$versionParts = $versionCore.Split('.')
$versionInfoVersion = "$($versionParts[0]).$($versionParts[1]).$($versionParts[2]).0"
$compiler = Find-InnoSetupCompiler -ExplicitPath $IsccPath

Write-Host "Building Subtitle Edit Bay $Version"
Write-Host "Inno Setup: $compiler"
Write-Host "Output: $resolvedOutputPath"

$compilerArguments = @(
    "/Qp",
    "/DSourceRoot=$projectRoot",
    "/DAppVersion=$Version",
    "/DVersionInfoVersion=$versionInfoVersion",
    "/DOutputDir=$outputDirectory",
    "/DOutputBaseFilename=$outputBaseFilename",
    $installerScript
)

& $compiler @compilerArguments
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path -LiteralPath $resolvedOutputPath -PathType Leaf)) {
    throw "Inno Setup completed without producing the expected file: $resolvedOutputPath"
}

if ($RequireSignature) {
    $signingScript = Join-Path $projectRoot "scripts\sign_windows_artifacts.ps1"
    & pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File $signingScript `
        -Path $resolvedOutputPath `
        -ExpectedSignerSubject $ExpectedSignerSubject `
        -TimestampServer $TimestampServer
    if ($LASTEXITCODE -ne 0) {
        throw "Installer Authenticode signing failed with exit code $LASTEXITCODE."
    }
    & "$PSScriptRoot\verify_windows_binary.ps1" `
        -Path $launcherPath `
        -ExpectedSignerSubject $ExpectedSignerSubject `
        -RequireSignature `
        -RequireTimestamp
    if ($LASTEXITCODE -ne 0) {
        throw "Signed launcher verification failed with exit code $LASTEXITCODE."
    }
    & "$PSScriptRoot\verify_windows_binary.ps1" `
        -Path $resolvedOutputPath `
        -ExpectedSignerSubject $ExpectedSignerSubject `
        -RequireSignature `
        -RequireTimestamp
    if ($LASTEXITCODE -ne 0) {
        throw "Signed installer verification failed with exit code $LASTEXITCODE."
    }
}

$artifact = Get-Item -LiteralPath $resolvedOutputPath
Write-Host "Created $($artifact.FullName) ($($artifact.Length) bytes)"
