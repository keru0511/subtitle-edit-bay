function Get-SetupMutexName {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $normalized = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd([char[]] "\\/").ToUpperInvariant()
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try { $digest = $sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($normalized)) }
    finally { $sha256.Dispose() }
    $identity = -join ($digest | ForEach-Object { $_.ToString("x2") })
    return "Local\SubtitleEditBay.Setup.$identity"
}

function Enter-SetupMutex {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $mutex = New-Object Threading.Mutex($false, (Get-SetupMutexName -ProjectRoot $ProjectRoot))
    $acquired = $false
    try { $acquired = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $acquired = $true }
    return [PSCustomObject]@{ Mutex = $mutex; Acquired = $acquired }
}

function Enter-SetupStartMutex {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $mutex = New-Object Threading.Mutex($false, ((Get-SetupMutexName -ProjectRoot $ProjectRoot) + ".Start"))
    $acquired = $false
    try { $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds(30)) }
    catch [Threading.AbandonedMutexException] { $acquired = $true }
    return [PSCustomObject]@{ Mutex = $mutex; Acquired = $acquired }
}

function Exit-SetupMutex {
    param($Lease)
    if (-not $Lease) { return }
    try { if ($Lease.Acquired) { $Lease.Mutex.ReleaseMutex() } }
    finally { $Lease.Mutex.Dispose() }
}

function Test-SetupMutexHeld {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $lease = Enter-SetupMutex -ProjectRoot $ProjectRoot
    if ($lease.Acquired) {
        Exit-SetupMutex -Lease $lease
        return $false
    }
    $lease.Mutex.Dispose()
    return $true
}

function Write-SetupStatus {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][ValidateSet("running", "failed", "success")][string]$Status,
        [string]$Message = "",
        [string]$Stage = "",
        [hashtable]$Details = @{}
    )
    $statusDirectory = Join-Path $ProjectRoot ".local"
    $statusPath = Join-Path $statusDirectory "setup-status.json"
    New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
    $versionPath = Join-Path $ProjectRoot "VERSION"
    $version = if (Test-Path -LiteralPath $versionPath -PathType Leaf) { (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8).Trim() } else { "" }
    $record = [ordered]@{
        schema_version = 1; status = $Status; app_version = $version; process_id = $PID
        stage = $Stage; message = $Message; details = $Details; updated_at = [DateTime]::UtcNow.ToString("o")
    }
    $temporaryPath = "$statusPath.$PID.tmp"
    [IO.File]::WriteAllText($temporaryPath, ($record | ConvertTo-Json -Depth 8) + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporaryPath -Destination $statusPath -Force
}
