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
    $Result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $ResultPath -Force
}

$oldVersion = "development"
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
    $actualHash = (Get-FileHash -LiteralPath $PackagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "Installer package checksum does not match."
    }

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

    Write-UpdateResult @{ status = "success"; old_version = $oldVersion; new_version = $newVersion; log = $ResultPath }
    Start-Process -FilePath $RestartExecutable -WorkingDirectory $InstallRoot -WindowStyle Hidden
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-UpdateResult @{ status = "rollback"; old_version = $oldVersion; new_version = $oldVersion; message = $message; log = $ResultPath }
    exit 1
}
