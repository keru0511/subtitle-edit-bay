param(
    [string]$ArchiveUrl = "https://github.com/keru0511/subtitle-edit-bay/archive/refs/heads/main.zip"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
Set-Location $projectRoot

$preservedTopLevel = @(
    ".git",
    ".venv",
    ".local",
    ".gui",
    "video_import",
    "video_export",
    "out",
    "__pycache__"
)
$preservedFiles = @(
    "assets/speaker_colors.json"
)

function Test-ProjectGitCheckout {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".git"))) {
        return $false
    }

    $git = Get-Command "git.exe" -ErrorAction SilentlyContinue
    if (-not $git) {
        return $false
    }

    $topLevel = & $git.Source -C $projectRoot rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $topLevel) {
        return $false
    }

    return [IO.Path]::GetFullPath($topLevel.Trim()).TrimEnd('\') -eq $projectRoot.TrimEnd('\')
}

function Update-GitCheckout {
    $localChanges = & git -C $projectRoot status --porcelain --untracked-files=no
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the repository status."
    }
    if ($localChanges) {
        throw "Tracked files have local changes. Commit or restore them before running update.bat. Personal videos and GUI settings are not affected."
    }

    $before = (& git -C $projectRoot rev-parse --short HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not determine the current version."
    }

    Write-Host "Updating Git checkout from $before..."
    & git -C $projectRoot pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "git pull failed. Check the remote URL, authentication, and current branch, then try again."
    }

    $after = (& git -C $projectRoot rev-parse --short HEAD).Trim()
    Write-Host "Source version: $before -> $after"
}

function Test-PreservedPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalizedPath = $RelativePath.Replace('\', '/')
    $topLevel = ($normalizedPath -split '/')[0]
    return $preservedTopLevel -contains $topLevel -or $preservedFiles -contains $normalizedPath
}

function Remove-UpdateTempDirectory {
    param([Parameter(Mandatory = $true)][string]$TempDirectory)

    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $resolved = [IO.Path]::GetFullPath($TempDirectory)
    $leaf = Split-Path -Leaf $resolved
    if ($resolved.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -and $leaf.StartsWith("subtitle-edit-bay-update-")) {
        Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Update-ZipDistribution {
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("subtitle-edit-bay-update-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tempRoot "latest.zip"
    $extractRoot = Join-Path $tempRoot "extracted"
    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null

    try {
        if (Test-Path -LiteralPath $ArchiveUrl -PathType Leaf) {
            Write-Host "Loading the specified ZIP distribution..."
            Copy-Item -LiteralPath $ArchiveUrl -Destination $zipPath -Force
        }
        else {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Write-Host "Downloading the latest ZIP distribution..."
            Invoke-WebRequest -UseBasicParsing -Uri $ArchiveUrl -OutFile $zipPath
        }
        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force

        $sourceRoot = Get-ChildItem -LiteralPath $extractRoot -Directory | Select-Object -First 1
        if (-not $sourceRoot -or
            -not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName "src")) -or
            -not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName "scripts\setup.ps1"))) {
            throw "The downloaded ZIP does not contain a valid Subtitle Edit Bay distribution."
        }

        $backupRoot = Join-Path $projectRoot (".local\update_backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
        $copiedFiles = 0
        foreach ($sourceFile in Get-ChildItem -LiteralPath $sourceRoot.FullName -File -Recurse) {
            $relativePath = $sourceFile.FullName.Substring($sourceRoot.FullName.Length).TrimStart([char[]]"\/")
            if (Test-PreservedPath -RelativePath $relativePath) {
                continue
            }

            $destination = Join-Path $projectRoot $relativePath
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                $backupPath = Join-Path $backupRoot $relativePath
                New-Item -ItemType Directory -Path (Split-Path -Parent $backupPath) -Force | Out-Null
                Copy-Item -LiteralPath $destination -Destination $backupPath -Force
            }

            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath $sourceFile.FullName -Destination $destination -Force
            $copiedFiles++
        }

        if ($copiedFiles -eq 0) {
            throw "The downloaded ZIP did not contain any update files."
        }

        Write-Host "Updated $copiedFiles application files from ZIP."
        if (Test-Path -LiteralPath $backupRoot) {
            Write-Host "Previous files were backed up to $backupRoot"
        }
    }
    finally {
        Remove-UpdateTempDirectory -TempDirectory $tempRoot
    }
}

if (Test-ProjectGitCheckout) {
    Update-GitCheckout
}
else {
    Update-ZipDistribution
}

$setupScript = Join-Path $projectRoot "scripts\setup.ps1"
if (-not (Test-Path -LiteralPath $setupScript)) {
    throw "The updated setup script is missing: $setupScript"
}

Write-Host "Refreshing dependencies..."
& $setupScript
