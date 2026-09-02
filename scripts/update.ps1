param(
    [string]$ArchiveUrl = "",
    [string]$ReleaseApiUrlOverride = "",
    [string]$ReleaseArchiveBaseUrlOverride = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
Set-Location $projectRoot

$releaseRepoOwner = "keru0511"
$releaseRepoName = "subtitle-edit-bay"
$releaseApiUrl = if ([string]::IsNullOrWhiteSpace($ReleaseApiUrlOverride)) {
    "https://api.github.com/repos/$releaseRepoOwner/$releaseRepoName/releases/latest"
}
else {
    $ReleaseApiUrlOverride
}
$releaseArchiveBaseUrl = if ([string]::IsNullOrWhiteSpace($ReleaseArchiveBaseUrlOverride)) {
    "https://github.com/$releaseRepoOwner/$releaseRepoName"
}
else {
    $ReleaseArchiveBaseUrlOverride.TrimEnd([char[]] "/")
}

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
$manifestRelativePath = ".local/update-manifest.json"

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

function Get-InstalledManifest {
    $manifestPath = Join-Path $projectRoot ($manifestRelativePath.Replace('/', '\'))
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        return @()
    }

    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($manifest -is [System.Array]) {
            return @($manifest | ForEach-Object { [string]$_ })
        }
        if ($manifest.files -is [System.Array]) {
            return @($manifest.files | ForEach-Object { [string]$_ })
        }
    }
    catch {
        Write-Warning "Could not read the previous application manifest. No files will be deleted."
    }

    return @()
}

function Write-InstalledManifest {
    param([Parameter(Mandatory = $true)][string[]]$Files)

    $manifestPath = Join-Path $projectRoot ($manifestRelativePath.Replace('/', '\'))
    New-Item -ItemType Directory -Path (Split-Path -Parent $manifestPath) -Force | Out-Null
    [IO.File]::WriteAllText(
        $manifestPath,
        (($Files | Sort-Object -Unique | ConvertTo-Json -Compress) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
}

function Get-LatestReleaseInfo {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $headers = @{ "User-Agent" = "subtitle-edit-bay-update" }
    $release = Invoke-RestMethod -Uri $releaseApiUrl -Headers $headers -ErrorAction Stop

    if (-not $release -or -not $release.tag_name) {
        throw "Could not resolve the latest GitHub release."
    }

    $tag = $release.tag_name.ToString()
    return @{
        tag = $tag
        version = Normalize-Version($tag)
    }
}

function Get-ReleaseArchiveUrl {
    param([Parameter(Mandatory = $true)][string]$TagName)

    return "$releaseArchiveBaseUrl/archive/refs/tags/$TagName.zip"
}

function Get-ApplicationVersion {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $versionFile = Join-Path $ProjectRoot "VERSION"
    if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) {
        return "development"
    }

    $raw = (Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8).Trim()
    if (-not $raw) {
        return "development"
    }
    if ($raw.StartsWith("v", [StringComparison]::Ordinal)) {
        return $raw
    }
    return "v$raw"
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
    $before = Get-ApplicationVersion -ProjectRoot $projectRoot
    Write-Host "Source version before update: $before"

    $localChanges = & git -C $projectRoot status --porcelain --untracked-files=no
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the repository status."
    }
    if ($localChanges) {
        throw "Tracked files have local changes. Commit or restore them before running update.bat. Personal videos and GUI settings are not affected."
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
    Write-Host "Source version: $before -> $(Get-ApplicationVersion -ProjectRoot $projectRoot) (commit $after)"
}

function Test-PreservedPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalizedPath = $RelativePath.Replace('\', '/')
    $topLevel = ($normalizedPath -split '/')[0]
    return $preservedTopLevel -contains $topLevel -or $preservedFiles -contains $normalizedPath
}

function Remove-UpdateTempDirectory {
    param([Parameter(Mandatory = $true)][string]$TempDirectory)

    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\\'
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
        Write-Host "Update failed. Restoring files from $BackupRoot"
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

function Update-ZipDistribution {
    $releaseInfo = if ([string]::IsNullOrWhiteSpace($ArchiveUrl)) { Get-LatestReleaseInfo } else { $null }
    $resolvedArchiveUrl = if ($releaseInfo) { Get-ReleaseArchiveUrl -TagName $releaseInfo.tag } else { $ArchiveUrl }

    if (-not $resolvedArchiveUrl) {
        throw "No update archive URL is available."
    }

    if ($releaseInfo) {
        Write-Host "Resolved latest release: $($releaseInfo.tag)"
    }
    $before = Get-ApplicationVersion -ProjectRoot $projectRoot
    Write-Host "Source version before update: $before"

    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("subtitle-edit-bay-update-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tempRoot "latest.zip"
    $extractRoot = Join-Path $tempRoot "extracted"
    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null

    $updatedFiles = New-Object System.Collections.ArrayList
    $addedFiles = New-Object System.Collections.ArrayList
    $removedFiles = New-Object System.Collections.ArrayList
    $currentVersion = Get-CurrentVersion
    $backupRoot = Join-Path $projectRoot (".local\update_backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    $copiedFiles = 0
    $previousManifest = @(Get-InstalledManifest)
    $manifestPath = Join-Path $projectRoot ($manifestRelativePath.Replace('/', '\'))

    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        $manifestBackupPath = Backup-File -FilePath $manifestPath -RelativePath $manifestRelativePath -BackupRoot $backupRoot
        [void]$updatedFiles.Add([pscustomobject]@{
            RelativePath = $manifestRelativePath
            DestinationPath = $manifestPath
            BackupPath = $manifestBackupPath
        })
    }
    else {
        [void]$addedFiles.Add($manifestPath)
    }

    try {
        if (Test-Path -LiteralPath $resolvedArchiveUrl -PathType Leaf) {
            Write-Host "Loading the specified ZIP distribution..."
            Copy-Item -LiteralPath $resolvedArchiveUrl -Destination $zipPath -Force
        }
        else {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Write-Host "Downloading ZIP distribution from $resolvedArchiveUrl"
            Invoke-WebRequest -UseBasicParsing -Uri $resolvedArchiveUrl -OutFile $zipPath
        }

        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force

        $sourceRoot = Get-ChildItem -LiteralPath $extractRoot -Directory | Select-Object -First 1
        if (-not $sourceRoot -or
            -not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName "src")) -or
            -not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName "scripts\setup.ps1"))) {
            throw "The downloaded ZIP does not contain a valid Subtitle Edit Bay distribution."
        }

        $archiveVersionPath = Join-Path $sourceRoot.FullName "VERSION"
        $archiveHasVersion = Test-Path -LiteralPath $archiveVersionPath -PathType Leaf
        $downloadedVersion = if ($archiveHasVersion) {
            Read-VersionFile -FilePath $archiveVersionPath
        }
        elseif ($releaseInfo) {
            $releaseInfo.version
        }
        else {
            "development"
        }
        if ($releaseInfo -and $archiveHasVersion -and $downloadedVersion -ne $releaseInfo.version) {
            throw "Downloaded archive version ($downloadedVersion) does not match release version ($($releaseInfo.version))."
        }

        Write-Host "Source version: $currentVersion -> $downloadedVersion"

        $sourceFiles = @{}
        foreach ($sourceFile in Get-ChildItem -LiteralPath $sourceRoot.FullName -File -Recurse) {
            $relativePath = Get-RelativePath -Root $sourceRoot.FullName -FullPath $sourceFile.FullName
            if (Test-PreservedPath -RelativePath $relativePath) {
                continue
            }
            $sourceFiles[$relativePath] = $sourceFile.FullName
        }

        $targetFiles = @{}
        foreach ($relativePath in $previousManifest) {
            $normalizedPath = ([string]$relativePath).Replace('/', '\')
            $destination = Join-Path $projectRoot $normalizedPath
            if ((Test-Path -LiteralPath $destination -PathType Leaf) -and -not (Test-PreservedPath -RelativePath $normalizedPath)) {
                $targetFiles[$normalizedPath] = $destination
            }
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
            if (-not $sourceFiles.ContainsKey($relativePath)) {
                $destination = Join-Path $projectRoot $relativePath
                $backupPath = Backup-File -FilePath $destination -RelativePath $relativePath -BackupRoot $backupRoot
                Remove-Item -LiteralPath $destination -Force
                [void]$removedFiles.Add([pscustomobject]@{
                    RelativePath = $relativePath
                    BackupPath = $backupPath
                })
            }
        }

        if ($releaseInfo -and -not $archiveHasVersion) {
            $versionPath = Join-Path $projectRoot "VERSION"
            if (Test-Path -LiteralPath $versionPath -PathType Leaf) {
                $backupPath = Backup-File -FilePath $versionPath -RelativePath "VERSION" -BackupRoot $backupRoot
                [void]$updatedFiles.Add([pscustomobject]@{
                    RelativePath = "VERSION"
                    DestinationPath = $versionPath
                    BackupPath = $backupPath
                })
            }
            else {
                [void]$addedFiles.Add($versionPath)
            }
            [IO.File]::WriteAllText($versionPath, "$downloadedVersion`n", [Text.UTF8Encoding]::new($false))
            $copiedFiles++
            $sourceFiles["VERSION"] = $versionPath
        }

        if ($copiedFiles -eq 0) {
            throw "The downloaded ZIP did not contain any update files."
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

        $postUpdateVersion = Get-CurrentVersion
        if ($postUpdateVersion -ne $downloadedVersion) {
            throw "Post-update version is not the expected release version."
        }

        Write-InstalledManifest -Files @($sourceFiles.Keys)

        Write-Host "Updated $copiedFiles application files from ZIP release $downloadedVersion."
        Write-Host "Previous files were backed up to $backupRoot"
        Write-Host "Updated $copiedFiles application files from ZIP."
        Write-Host "Source version after update: $(Get-ApplicationVersion -ProjectRoot $projectRoot)"
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

    $setupScript = Join-Path $projectRoot "scripts\setup.ps1"
    if (-not (Test-Path -LiteralPath $setupScript)) {
        throw "The setup script is missing: $setupScript"
    }

    Write-Host "Refreshing dependencies..."
    $LASTEXITCODE = 0
    & $setupScript
    if ($LASTEXITCODE -ne 0) {
        throw "Setup failed while refreshing dependencies."
    }
}
else {
    Update-ZipDistribution
}
