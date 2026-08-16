param(
    [string]$ArchiveUrl = ""
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

$releaseRepoOwner = "keru0511"
$releaseRepoName = "subtitle-edit-bay"
$releaseApiUrl = "https://api.github.com/repos/$releaseRepoOwner/$releaseRepoName/releases/latest"

function Normalize-Version {
    param([Parameter(Mandatory = $true)][string]$Value)

    $trimmed = $Value.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) {
        return "development"
    }

    if ($trimmed -eq "development") {
        return "development"
    }

    if ($trimmed.StartsWith("v", [StringComparison]::OrdinalIgnoreCase)) {
        return $trimmed.ToLowerInvariant()
    }

    return "v$trimmed"
}

function Read-VersionFile {
    param([Parameter(Mandatory = $true)][string]$FilePath)

    if (-not (Test-Path -LiteralPath $FilePath)) {
        return "development"
    }

    $raw = Get-Content -LiteralPath $FilePath -Raw -Encoding UTF8
    return Normalize-Version($raw)
}

function Get-CurrentVersion {
    return Read-VersionFile -FilePath (Join-Path $projectRoot "VERSION")
}

function Get-RelativePath {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$FullPath)

    return $FullPath.Substring($Root.Length).TrimStart([char[]] "/\\")
}

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
    Write-Host ("Source version before update: {0}" -f (Get-CurrentVersion))

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

    Write-Host "Updating Git checkout..."
    & git -C $projectRoot pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "git pull failed. Check the remote URL, authentication, and current branch, then try again."
    }

    $after = (& git -C $projectRoot rev-parse --short HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not determine version after update."
    }

    Write-Host ("Source version: {0} -> {1} (commit {2})" -f $before, (Get-CurrentVersion), $after)
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

function Backup-File {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$BackupRoot
    )

    $backupPath = Join-Path $BackupRoot $RelativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $backupPath) -Force | Out-Null
    Copy-Item -LiteralPath $FilePath -Destination $backupPath -Force
    return $backupPath
}

function Restore-UpdateState {
    param(
        [Parameter(Mandatory = $true)][string]$BackupRoot,
        [Parameter(Mandatory = $true)]$UpdatedFiles,
        [Parameter(Mandatory = $true)]$AddedFiles,
        [Parameter(Mandatory = $true)]$RemovedFiles
    )

    if (Test-Path -LiteralPath $BackupRoot) {
        Write-Host ("Update failed. Restoring files from {0}" -f $BackupRoot)
    }

    foreach ($entry in $RemovedFiles) {
        $destination = Join-Path $projectRoot $entry.RelativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $entry.BackupPath -Destination $destination -Force
    }

    foreach ($entry in $UpdatedFiles) {
        Copy-Item -LiteralPath $entry.BackupPath -Destination $entry.DestinationPath -Force
    }

    foreach ($path in $AddedFiles) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
}

function Get-LatestReleaseInfo {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $headers = @{ "User-Agent" = "subtitle-edit-bay-update" }
    $release = Invoke-RestMethod -Uri $releaseApiUrl -Headers $headers -ErrorAction Stop

    if (-not $release -or -not $release.tag_name) {
        throw "Could not resolve the latest GitHub release."
    }

    return @{
        tag = $release.tag_name.ToString()
        version = Normalize-Version($release.tag_name.ToString())
    }
}

function Get-ReleaseArchiveUrl {
    param([Parameter(Mandatory = $true)][string]$TagName)

    return "https://github.com/$releaseRepoOwner/$releaseRepoName/archive/refs/tags/$TagName.zip"
}

function Update-ZipDistribution {
    $releaseInfo = $null
    if ([string]::IsNullOrWhiteSpace($ArchiveUrl)) {
        $releaseInfo = Get-LatestReleaseInfo
        Write-Host ("Resolved latest release: {0}" -f $releaseInfo.tag)
    }

    $resolvedArchiveUrl = if ($releaseInfo) { Get-ReleaseArchiveUrl -TagName $releaseInfo.tag } else { $ArchiveUrl }
    if ([string]::IsNullOrWhiteSpace($resolvedArchiveUrl)) {
        throw "No update archive URL is available."
    }

    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("subtitle-edit-bay-update-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tempRoot "latest.zip"
    $extractRoot = Join-Path $tempRoot "extracted"
    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null

    $backupRoot = Join-Path $projectRoot (".local\update_backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    $updatedFiles = New-Object System.Collections.ArrayList
    $addedFiles = New-Object System.Collections.ArrayList
    $removedFiles = New-Object System.Collections.ArrayList
    $copiedFiles = 0

    try {
        if (Test-Path -LiteralPath $resolvedArchiveUrl -PathType Leaf) {
            Write-Host "Loading the specified ZIP distribution..."
            Copy-Item -LiteralPath $resolvedArchiveUrl -Destination $zipPath -Force
        }
        else {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Write-Host ("Downloading ZIP distribution from {0}..." -f $resolvedArchiveUrl)
            Invoke-WebRequest -UseBasicParsing -Uri $resolvedArchiveUrl -OutFile $zipPath
        }
        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force

        $sourceRoot = Get-ChildItem -LiteralPath $extractRoot -Directory | Select-Object -First 1
        if (-not $sourceRoot -or
            -not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName "src")) -or
            -not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName "scripts\setup.ps1"))) {
            throw "The downloaded ZIP does not contain a valid Subtitle Edit Bay distribution."
        }

        if ($releaseInfo) {
            $downloadedVersion = Read-VersionFile -FilePath (Join-Path $sourceRoot.FullName "VERSION")
            if ($downloadedVersion -ne $releaseInfo.version) {
                throw ("Downloaded archive version ({0}) does not match release version ({1})." -f $downloadedVersion, $releaseInfo.version)
            }
            Write-Host ("Source version: {0} -> {1}" -f (Get-CurrentVersion), $downloadedVersion)
        }

        $sourceFiles = @{}
        foreach ($sourceFile in Get-ChildItem -LiteralPath $sourceRoot.FullName -File -Recurse) {
            $relativePath = Get-RelativePath -Root $sourceRoot.FullName -FullPath $sourceFile.FullName
            if (Test-PreservedPath -RelativePath $relativePath) {
                continue
            }
            $sourceFiles[$relativePath] = $sourceFile.FullName
        }

        $targetFiles = @{}
        foreach ($targetFile in Get-ChildItem -LiteralPath $projectRoot -File -Recurse) {
            $relativePath = Get-RelativePath -Root $projectRoot -FullPath $targetFile.FullName
            if (Test-PreservedPath -RelativePath $relativePath) {
                continue
            }
            $targetFiles[$relativePath] = $targetFile.FullName
        }

        foreach ($relativePath in $sourceFiles.Keys) {
            $sourceFilePath = $sourceFiles[$relativePath]
            $destination = Join-Path $projectRoot $relativePath
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                $backupPath = Backup-File -FilePath $destination -RelativePath $relativePath -BackupRoot $backupRoot
                [void]$updatedFiles.Add([pscustomobject]@{
                    RelativePath = $relativePath
                    DestinationPath = $destination
                    BackupPath = $backupPath
                })
            }
            else {
                [void]$addedFiles.Add($destination)
            }

            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath $sourceFilePath -Destination $destination -Force
            $copiedFiles++
        }

        foreach ($relativePath in $targetFiles.Keys) {
            if ($sourceFiles.ContainsKey($relativePath)) {
                continue
            }

            $destination = Join-Path $projectRoot $relativePath
            $backupPath = Backup-File -FilePath $destination -RelativePath $relativePath -BackupRoot $backupRoot
            Remove-Item -LiteralPath $destination -Force
            [void]$removedFiles.Add([pscustomobject]@{
                RelativePath = $relativePath
                BackupPath = $backupPath
            })
        }

        $setupScript = Join-Path $projectRoot "scripts\setup.ps1"
        if (-not (Test-Path -LiteralPath $setupScript)) {
            throw "The updated setup script is missing: $setupScript"
        }

        Write-Host "Refreshing dependencies..."
        $LASTEXITCODE = 0
        & $setupScript
        if ($LASTEXITCODE -ne 0) {
            throw "Setup failed while refreshing dependencies."
        }

        if ($releaseInfo) {
            $postUpdateVersion = Get-CurrentVersion
            if ($postUpdateVersion -ne $releaseInfo.version) {
                throw "Post-update version is not the expected release version."
            }
        }

        if ($copiedFiles -eq 0) {
            throw "The downloaded ZIP did not contain any update files."
        }

        Write-Host "Updated $copiedFiles application files from ZIP."
        if (Test-Path -LiteralPath $backupRoot) {
            Write-Host "Previous files were backed up to $backupRoot"
        }
        if ($releaseInfo) {
            Write-Host ("Updated application to version {0} from ZIP release." -f $releaseInfo.version)
        }
    }
    catch {
        if (Test-Path -LiteralPath $backupRoot) {
            try {
                Restore-UpdateState -BackupRoot $backupRoot -UpdatedFiles $updatedFiles -AddedFiles $addedFiles -RemovedFiles $removedFiles
            }
            catch {
                throw "Update failed and rollback failed: $($_.Exception.Message)"
            }
        }

        throw $_
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

