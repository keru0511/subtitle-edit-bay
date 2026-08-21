param(
    [string]$OutputPath = "dist\SubtitleEditBayLauncher.exe",
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

$compiler = Get-Command cl.exe -ErrorAction SilentlyContinue
if (-not $compiler) {
    if ($AllowMissingCompiler) {
        Write-Warning "cl.exe was not found. The installer will use the PowerShell launcher fallback."
        exit 0
    }
    throw "Visual C++ cl.exe was not found. Run from a Visual Studio Developer PowerShell or use -AllowMissingCompiler."
}

$outputDirectory = Split-Path -Parent $resolvedOutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$objectDirectory = Join-Path ([IO.Path]::GetTempPath()) ("subtitle-edit-bay-launcher-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $objectDirectory -Force | Out-Null
try {
    Push-Location $objectDirectory
    & $compiler.Source /nologo /O2 /W4 /DUNICODE /D_UNICODE $sourcePath /Fe:$resolvedOutputPath /link /SUBSYSTEM:WINDOWS
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
Write-Host "Created $resolvedOutputPath"
