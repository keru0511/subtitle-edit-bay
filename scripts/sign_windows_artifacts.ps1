param(
    [Parameter(Mandatory = $true)][string[]]$Path,
    [Parameter(Mandatory = $true)][string]$ExpectedSignerSubject,
    [string]$TimestampServer = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot/windows_signing_identity.ps1"
$encodedCertificate = $env:WINDOWS_SIGNING_CERTIFICATE_BASE64
$certificatePassword = $env:WINDOWS_SIGNING_CERTIFICATE_PASSWORD
if (-not $encodedCertificate -or -not $certificatePassword) {
    throw "Windows signing credentials are unavailable; refusing to produce unsigned release artifacts."
}

try {
    $certificateBytes = [Convert]::FromBase64String($encodedCertificate)
    $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $certificateBytes,
        $certificatePassword,
        [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
    )
    if (-not $certificate.HasPrivateKey) {
        throw "Windows signing certificate has no private key."
    }
    Assert-ExactCertificateSubject -Certificate $certificate -ExpectedSubject $ExpectedSignerSubject
    foreach ($candidate in $Path) {
        $resolved = (Resolve-Path -LiteralPath $candidate).Path
        $result = Set-AuthenticodeSignature `
            -LiteralPath $resolved `
            -Certificate $certificate `
            -HashAlgorithm SHA256 `
            -TimestampServer $TimestampServer
        if ($result.Status -ne "Valid") {
            throw "Authenticode signing failed for $resolved`: $($result.Status) $($result.StatusMessage)"
        }
        & "$PSScriptRoot/verify_windows_binary.ps1" `
            -Path $resolved `
            -ExpectedSignerSubject $ExpectedSignerSubject `
            -RequireSignature `
            -RequireTimestamp
    }
}
finally {
    $certificatePassword = $null
    $encodedCertificate = $null
    if ($certificate) {
        $certificate.Dispose()
    }
}
