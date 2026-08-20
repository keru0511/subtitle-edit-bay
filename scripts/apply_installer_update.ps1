param(
    [Parameter(Mandatory = $true)][string]$PackagePath,
    [Parameter(Mandatory = $true)][int]$ParentPid,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$RestartExecutable,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$ExpectedSha256,
    [Parameter(Mandatory = $true)][string]$ResultPath
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-UpdateResult {
    param([Parameter(Mandatory = $true)][hashtable]$Result)
    $parent = Split-Path -Parent $ResultPath
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary = "$ResultPath.$([Guid]::NewGuid().ToString('N')).tmp"
    $json = $Result | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $ResultPath -Force
}

function Test-RecoveryPreservedPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = $RelativePath.Replace('\', '/')
    $topLevel = ($normalized -split '/')[0]
    return @('.git', '.venv', '.local', '.gui', 'video_import', 'video_export', 'out', '__pycache__') -contains $topLevel
}

function Get-RecoveryRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$FullPath
    )

    return $FullPath.Substring($Root.Length).TrimStart([char[]] "/\")
}

function New-RecoverySnapshot {
    param([Parameter(Mandatory = $true)][string]$Root)

    $snapshotRoot = Join-Path ([IO.Path]::GetTempPath()) ("subtitle-edit-bay-recovery-" + [guid]::NewGuid().ToString("N"))
    $filesRoot = Join-Path $snapshotRoot "files"
    New-Item -ItemType Directory -Path $filesRoot -Force | Out-Null
    $entries = New-Object System.Collections.ArrayList
    $files = @(Get-ChildItem -LiteralPath $Root -Force -File -Recurse)
    foreach ($file in $files) {
        $relative = Get-RecoveryRelativePath -Root $Root -FullPath $file.FullName
        if (Test-RecoveryPreservedPath -RelativePath $relative) {
            continue
        }
        $destination = Join-Path $filesRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        [void]$entries.Add($relative.Replace('\', '/'))
    }
    [IO.File]::WriteAllText(
        (Join-Path $snapshotRoot "manifest.json"),
        (($entries | Sort-Object -Unique | ConvertTo-Json -Compress) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    return $snapshotRoot
}

function Restore-RecoverySnapshot {
    param([Parameter(Mandatory = $true)][string]$SnapshotRoot)

    $manifestPath = Join-Path $SnapshotRoot "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Recovery snapshot manifest is missing: $manifestPath"
    }
    $manifestValue = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $expected = @{}
    foreach ($entry in @($manifestValue)) {
        $relative = ([string]$entry).Replace('/', '\')
        if (-not $relative -or (Test-RecoveryPreservedPath -RelativePath $relative)) {
            continue
        }
        $expected[$relative] = $true
    }

    $currentFiles = @(Get-ChildItem -LiteralPath $InstallRoot -Force -File -Recurse)
    foreach ($file in $currentFiles) {
        $relative = Get-RecoveryRelativePath -Root $InstallRoot -FullPath $file.FullName
        if (Test-RecoveryPreservedPath -RelativePath $relative) {
            continue
        }
        if (-not $expected.ContainsKey($relative)) {
            Remove-Item -LiteralPath $file.FullName -Force
        }
    }

    foreach ($relative in $expected.Keys) {
        $source = Join-Path (Join-Path $SnapshotRoot "files") $relative
        $destination = Join-Path $InstallRoot $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Recovery snapshot file is missing: $relative"
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

function Get-PackageSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $getFileHash = Get-Command "Get-FileHash" -ErrorAction SilentlyContinue
    if ($getFileHash) {
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash([IO.File]::ReadAllBytes($Path)))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

$oldVersion = "development"
$recoveryRoot = ""
try {
    $versionPath = Join-Path $InstallRoot "VERSION"
    if (Test-Path -LiteralPath $versionPath -PathType Leaf) {
        $oldVersion = (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8).Trim()
    }

    $waitUntil = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $waitUntil) {
        $parent = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
        if (-not $parent) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        throw "GUI process did not exit before the update timeout."
    }

    if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
        throw "Downloaded installer package is missing."
    }
    $actualHash = Get-PackageSha256 -Path $PackagePath
    if ($actualHash -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "Installer package checksum does not match."
    }

    $recoveryRoot = New-RecoverySnapshot -Root $InstallRoot

    $installer = Start-Process -FilePath $PackagePath `
        -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS") `
        -WorkingDirectory $InstallRoot -WindowStyle Hidden -Wait -PassThru
    if ($installer.ExitCode -ne 0) {
        throw "Installer exited with code $($installer.ExitCode)."
    }

    if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf)) {
        throw "Updated VERSION file is missing."
    }
    $newVersion = (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8).Trim()
    if ($newVersion.TrimStart('v') -ne $ExpectedVersion.TrimStart('v')) {
        throw "Installed version $newVersion does not match $ExpectedVersion."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot "scripts\launch.ps1") -PathType Leaf)) {
        throw "Updated launcher script is missing."
    }
    if (-not (Test-Path -LiteralPath $RestartExecutable -PathType Leaf)) {
        throw "Restart executable is missing: $RestartExecutable"
    }

    if ($recoveryRoot -and (Test-Path -LiteralPath $recoveryRoot)) {
        Remove-Item -LiteralPath $recoveryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-UpdateResult @{ status = "success"; old_version = $oldVersion; new_version = $newVersion; log = $ResultPath }
    Start-Process -FilePath $RestartExecutable -WorkingDirectory $InstallRoot -WindowStyle Hidden
    exit 0
}
catch {
    $message = $_.Exception.Message
    $rollbackRestored = $false
    $rollbackError = ""
    if ($recoveryRoot -and (Test-Path -LiteralPath $recoveryRoot)) {
        try {
            Restore-RecoverySnapshot -SnapshotRoot $recoveryRoot
            $restoredVersion = Get-Content -LiteralPath (Join-Path $InstallRoot "VERSION") -Raw -Encoding UTF8
            if ($restoredVersion.Trim().TrimStart('v') -ne $oldVersion.Trim().TrimStart('v')) {
                throw "Restored VERSION does not match the previous version."
            }
            $rollbackRestored = $true
        }
        catch {
            $rollbackError = $_.Exception.Message
        }
    }
    if ($rollbackRestored) {
        Write-UpdateResult @{ status = "rollback"; rollback_restored = $true; old_version = $oldVersion; new_version = $oldVersion; message = $message; recovery_snapshot = $recoveryRoot; log = $ResultPath }
    }
    else {
        Write-UpdateResult @{ status = "rollback_failed"; rollback_restored = $false; old_version = $oldVersion; new_version = $oldVersion; message = $message; rollback_error = $rollbackError; recovery_snapshot = $recoveryRoot; log = $ResultPath }
    }
    exit 1
}
