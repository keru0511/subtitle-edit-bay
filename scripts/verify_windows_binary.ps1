param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$ExpectedVersion,
    [string]$ExpectedProductName,
    [string]$ExpectedPublisher,
    [string]$ExpectedSignerSubject,
    [switch]$CheckDependencies,
    [switch]$RequireSignature,
    [switch]$RequireTimestamp
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot/windows_signing_identity.ps1"
$binary = Get-Item -LiteralPath $Path -ErrorAction Stop
if ($binary.Extension -ne ".exe") {
    throw "Windows binary must be an .exe file: $Path"
}

$version = $binary.VersionInfo
if ($ExpectedVersion -and $version.ProductVersion.Trim() -ne $ExpectedVersion) {
    throw "ProductVersion mismatch: expected $ExpectedVersion, got $($version.ProductVersion)"
}
if ($ExpectedProductName -and $version.ProductName.Trim() -ne $ExpectedProductName) {
    throw "ProductName mismatch: expected $ExpectedProductName, got $($version.ProductName)"
}
if ($ExpectedPublisher -and $version.CompanyName.Trim() -ne $ExpectedPublisher) {
    throw "CompanyName mismatch: expected $ExpectedPublisher, got $($version.CompanyName)"
}

if ($CheckDependencies) {
    $dumpbin = Get-Command dumpbin.exe -ErrorAction SilentlyContinue
    if (-not $dumpbin) {
        throw "dumpbin.exe is required to verify launcher dependencies."
    }
    $headers = (& $dumpbin.Source /HEADERS $binary.FullName | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "dumpbin PE header inspection failed with exit code $LASTEXITCODE."
    }
    if ($headers -notmatch '(?im)^\s*866 machine \(x64\)') {
        throw "Launcher must be an x64 PE image."
    }
    if ($headers -notmatch '(?im)^\s*2 subsystem \(Windows GUI\)') {
        throw "Launcher must use the Windows GUI subsystem."
    }
    $imports = (& $dumpbin.Source /DEPENDENTS $binary.FullName | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "dumpbin dependency inspection failed with exit code $LASTEXITCODE."
    }
    $forbidden = @(
        "VCRUNTIME",
        "MSVCP",
        "MSVCR",
        "CONCRT",
        "UCRTBASE.DLL",
        "API-MS-WIN-CRT-",
        "EXT-MS-WIN-CRT-"
    )
    foreach ($name in $forbidden) {
        if ($imports.IndexOf($name, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            throw "Launcher has a forbidden external C/C++ runtime dependency: $name"
        }
    }
}

if ($RequireSignature) {
    $signature = Get-AuthenticodeSignature -LiteralPath $binary.FullName
    if ($signature.Status -ne "Valid" -or -not $signature.SignerCertificate) {
        throw "Authenticode signature is not valid: $($signature.Status) $($signature.StatusMessage)"
    }
    if (-not $ExpectedSignerSubject) {
        throw "ExpectedSignerSubject is required when Authenticode signature verification is enabled."
    }
    Assert-ExactCertificateSubject -Certificate $signature.SignerCertificate -ExpectedSubject $ExpectedSignerSubject
    if ($RequireTimestamp -and -not $signature.TimeStamperCertificate) {
        throw "Authenticode signature does not contain a trusted timestamp."
    }
}

Write-Host "Verified Windows binary contract: $($binary.FullName)"
