function Get-NormalizedCertificateSubject {
    param([Parameter(Mandatory = $true)][string]$Subject)

    if (-not $Subject.Contains("=")) {
        throw "Expected signer subject must be a complete X.500 distinguished name."
    }
    try {
        $distinguishedName = [Security.Cryptography.X509Certificates.X500DistinguishedName]::new($Subject)
        return [Convert]::ToBase64String($distinguishedName.RawData)
    }
    catch {
        throw "Expected signer subject is not a valid X.500 distinguished name: $_"
    }
}

function Assert-ExactCertificateSubject {
    param(
        [Parameter(Mandatory = $true)]$Certificate,
        [Parameter(Mandatory = $true)][string]$ExpectedSubject
    )

    $expectedIdentity = Get-NormalizedCertificateSubject -Subject $ExpectedSubject
    $actualIdentity = [Convert]::ToBase64String($Certificate.SubjectName.RawData)
    if (-not [string]::Equals($actualIdentity, $expectedIdentity, [StringComparison]::Ordinal)) {
        throw "Authenticode signer subject does not exactly match the trusted X.500 subject."
    }
}
