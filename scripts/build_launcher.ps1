param(
    [string]$OutputPath = "dist\SubtitleEditBayLauncher.exe",
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version,
    [string]$Publisher = "Subtitle Edit Bay",
    [string]$IconPath,
    [switch]$AllowMissingCompiler
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$sourcePath = Join-Path $projectRoot "launcher\SubtitleEditBayLauncher.c"
$resolvedOutputPath = if ([IO.Path]::IsPathRooted($OutputPath)) {
    [IO.Path]::GetFullPath($OutputPath)
}
else {
    [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
}

if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Launcher source is missing: $sourcePath"
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
    if ($AllowMissingCompiler) {
        Write-Warning "cl.exe was not found. The installer will use the PowerShell launcher fallback."
        exit 0
    }
    throw "Visual C++ cl.exe was not found. Run from a Visual Studio Developer PowerShell or use -AllowMissingCompiler."
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
    $resourceLines = @(
        "#include <windows.h>",
        "1 VERSIONINFO",
        "FILEVERSION $versionQuad",
        "PRODUCTVERSION $versionQuad",
        "FILEFLAGSMASK 0x3fL",
        "FILEOS 0x40004L",
        "FILETYPE 0x1L",
        "BEGIN",
        '  BLOCK "StringFileInfo"',
        "  BEGIN",
        '    BLOCK "041104b0"',
        "    BEGIN",
        '      VALUE "CompanyName", "' + $Publisher.Replace('"', '\"') + '\0"',
        '      VALUE "FileDescription", "Subtitle Edit Bay launcher\0"',
        '      VALUE "FileVersion", "' + $versionCore + '\0"',
        '      VALUE "ProductName", "Subtitle Edit Bay\0"',
        '      VALUE "ProductVersion", "' + $versionCore + '\0"',
        "    END",
        "  END",
        '  BLOCK "VarFileInfo"',
        "  BEGIN",
        '    VALUE "Translation", 0x411, 1200',
        "  END",
        "END"
    )
    if ($IconPath) {
        $resolvedIcon = (Resolve-Path -LiteralPath $IconPath).Path.Replace('\', '\\')
        $resourceLines = @('101 ICON "' + $resolvedIcon + '"') + $resourceLines
    }
    [IO.File]::WriteAllLines($resourcePath, $resourceLines, [Text.UTF8Encoding]::new($false))
    & $resourceCompiler.Source /nologo /fo SubtitleEditBayLauncher.res $resourcePath
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher resource compilation failed with exit code $LASTEXITCODE."
    }
    # /MT makes the launcher independent of the separately installed VC runtime.
    & $compiler.Source /nologo /O2 /W4 /MT $sourcePath SubtitleEditBayLauncher.res user32.lib /Fe:$resolvedOutputPath /link /SUBSYSTEM:WINDOWS
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

& "$PSScriptRoot/verify_windows_binary.ps1" `
    -Path $resolvedOutputPath `
    -ExpectedVersion $versionCore `
    -ExpectedProductName "Subtitle Edit Bay" `
    -ExpectedPublisher $Publisher `
    -CheckDependencies
if ($LASTEXITCODE -ne 0) {
    throw "Launcher binary contract verification failed with exit code $LASTEXITCODE."
}
Write-Host "Created $resolvedOutputPath"
