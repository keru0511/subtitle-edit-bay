param(
    [string]$OutputPath = "dist\SubtitleEditBayLauncher.exe",
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version,
    [string]$Publisher = "Subtitle Edit Bay",
    [string]$IconPath,
    [switch]$RequireProductIcon
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$sourcePath = Join-Path $projectRoot "launcher\SubtitleEditBayLauncher.c"
$resourceTemplatePath = Join-Path $projectRoot "launcher\SubtitleEditBayLauncher.rc"
$resolvedOutputPath = if ([IO.Path]::IsPathRooted($OutputPath)) {
    [IO.Path]::GetFullPath($OutputPath)
}
else {
    [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
}

if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Launcher source is missing: $sourcePath"
}
if (-not (Test-Path -LiteralPath $resourceTemplatePath -PathType Leaf)) {
    throw "Launcher resource definition is missing: $resourceTemplatePath"
}

if ($RequireProductIcon -and -not $IconPath) {
    throw "A product icon path is required when -RequireProductIcon is specified."
}
if ($IconPath -and -not (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
    throw "Launcher product icon is missing: $IconPath"
}
$resolvedIconPath = if ($IconPath) {
    (Resolve-Path -LiteralPath $IconPath).Path
}
else {
    $null
}

function Initialize-MsvcEnvironment {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
        return
    }
    $installation = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
    if (-not $installation) {
        return
    }
    $developerCommand = Join-Path $installation "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path -LiteralPath $developerCommand -PathType Leaf)) {
        return
    }
    $environment = & cmd.exe /s /c "`"$developerCommand`" -no_logo -arch=x64 -host_arch=x64 >nul && set"
    if ($LASTEXITCODE -ne 0) {
        throw "Visual Studio developer environment initialization failed."
    }
    foreach ($line in $environment) {
        $separator = $line.IndexOf('=')
        if ($separator -gt 0) {
            [Environment]::SetEnvironmentVariable($line.Substring(0, $separator), $line.Substring($separator + 1))
        }
    }
}

$compiler = Get-Command cl.exe -ErrorAction SilentlyContinue
if (-not $compiler) {
    Initialize-MsvcEnvironment
    $compiler = Get-Command cl.exe -ErrorAction SilentlyContinue
}
if (-not $compiler) {
    throw "Visual C++ cl.exe was not found. Run from a Visual Studio Developer PowerShell."
}

$resourceCompiler = Get-Command rc.exe -ErrorAction SilentlyContinue
if (-not $resourceCompiler) {
    throw "Windows resource compiler rc.exe was not found in the Visual Studio environment."
}

$outputDirectory = Split-Path -Parent $resolvedOutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$objectDirectory = Join-Path ([IO.Path]::GetTempPath()) ("subtitle-edit-bay-launcher-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $objectDirectory -Force | Out-Null
try {
    Push-Location $objectDirectory
    $versionCore = ($Version -split '[-+]')[0]
    $versionParts = $versionCore.Split('.')
    $versionQuad = "$($versionParts[0]),$($versionParts[1]),$($versionParts[2]),0"
    $resourcePath = Join-Path $objectDirectory "SubtitleEditBayLauncher.rc"
    function ConvertTo-RcString {
        param([Parameter(Mandatory = $true)][string]$Value)

        return $Value.Replace('\', '\\').Replace('"', '\"')
    }

    $iconResource = ""
    if ($resolvedIconPath) {
        $resolvedIcon = $resolvedIconPath.Replace('\', '\\')
        $iconResource = '101 ICON "' + $resolvedIcon + '"'
    }

    $resourceText = [IO.File]::ReadAllText($resourceTemplatePath, [Text.UTF8Encoding]::new($false))
    $resourceText = $resourceText.Replace('@ICON_RESOURCE@', $iconResource)
    $resourceText = $resourceText.Replace('@VERSION_QUAD@', $versionQuad)
    $resourceText = $resourceText.Replace('@VERSION_CORE@', (ConvertTo-RcString $versionCore))
    $resourceText = $resourceText.Replace('@PUBLISHER@', (ConvertTo-RcString $Publisher))
    [IO.File]::WriteAllText($resourcePath, $resourceText, [Text.UTF8Encoding]::new($false))
    & $resourceCompiler.Source /nologo /fo SubtitleEditBayLauncher.res $resourcePath
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher resource compilation failed with exit code $LASTEXITCODE."
    }
    # /MT makes the launcher independent of the separately installed VC runtime.
    & $compiler.Source /nologo /O2 /W4 /MT $sourcePath SubtitleEditBayLauncher.res user32.lib shell32.lib /Fe:$resolvedOutputPath /link /SUBSYSTEM:WINDOWS /MACHINE:X64
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher compilation failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $objectDirectory -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $resolvedOutputPath -PathType Leaf)) {
    throw "Launcher compilation completed without producing $resolvedOutputPath"
}

$verificationArguments = @(
    "-Path", $resolvedOutputPath,
    "-ExpectedVersion", $versionCore,
    "-ExpectedFileVersion", $versionCore,
    "-ExpectedFileDescription", "Subtitle Edit Bay launcher",
    "-ExpectedProductName", "Subtitle Edit Bay",
    "-ExpectedPublisher", $Publisher,
    "-CheckDependencies"
)
if ($IconPath -or $RequireProductIcon) {
    $verificationArguments += "-RequireProductIcon"
}
& "$PSScriptRoot/verify_windows_binary.ps1" @verificationArguments
if ($LASTEXITCODE -ne 0) {
    throw "Launcher binary contract verification failed with exit code $LASTEXITCODE."
}
Write-Host "Created $resolvedOutputPath"
