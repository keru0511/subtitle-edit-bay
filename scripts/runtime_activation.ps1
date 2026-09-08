function Set-ActiveRuntimeGeneration {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$NewRuntimeDirectory,
        [Parameter(Mandatory = $true)][string]$CandidateManifestPath,
        [Parameter(Mandatory = $true)][string]$ActiveManifestPath,
        [Parameter(Mandatory = $true)][scriptblock]$VerifyScript,
        [string[]]$CleanupDirectories = @(),
        [scriptblock]$RemoveDirectoryScript = $null
    )

    $newRuntime = [IO.Path]::GetFullPath($NewRuntimeDirectory)
    $candidateManifest = [IO.Path]::GetFullPath($CandidateManifestPath)
    $activeManifest = [IO.Path]::GetFullPath($ActiveManifestPath)
    $manifestDirectory = [IO.Path]::GetDirectoryName($activeManifest)
    New-Item -ItemType Directory -Path $manifestDirectory -Force | Out-Null
    if (-not (Test-Path -LiteralPath $newRuntime -PathType Container)) {
        throw "New runtime directory is missing: $newRuntime"
    }
    if (-not (Test-Path -LiteralPath $candidateManifest -PathType Leaf)) {
        throw "Candidate runtime manifest is missing: $candidateManifest"
    }

    $previousManifest = Join-Path $manifestDirectory ("runtime-manifest.previous-{0}.json" -f [Guid]::NewGuid().ToString("N"))
    $hadActiveManifest = Test-Path -LiteralPath $activeManifest -PathType Leaf
    $published = $false
    $committed = $false
    try {
        if ($hadActiveManifest) {
            [IO.File]::Replace($candidateManifest, $activeManifest, $previousManifest, $true)
        } else {
            [IO.File]::Move($candidateManifest, $activeManifest)
        }
        $published = $true

        & $VerifyScript
        $committed = $true
    } catch {
        $activationError = $_
        if ($published -and -not $committed) {
            try {
                if ($hadActiveManifest -and (Test-Path -LiteralPath $previousManifest -PathType Leaf)) {
                    [IO.File]::Replace($previousManifest, $activeManifest, $null, $true)
                } elseif (-not $hadActiveManifest -and (Test-Path -LiteralPath $activeManifest -PathType Leaf)) {
                    Remove-Item -LiteralPath $activeManifest -Force
                }
            } catch {
                throw "Runtime activation failed and the previous manifest could not be restored: $activationError; restore error: $_"
            }
        }
        throw $activationError
    }

    # Activation is committed once the new manifest and runtime pass verification.
    # Cleanup is deliberately outside the rollback boundary: a partial deletion of
    # an old generation must never replace or remove the verified active runtime.
    $cleanupTargets = @($previousManifest) + @($CleanupDirectories)
    foreach ($target in $cleanupTargets) {
        if (-not $target -or -not (Test-Path -LiteralPath $target)) { continue }
        try {
            if ($RemoveDirectoryScript -and (Test-Path -LiteralPath $target -PathType Container)) {
                & $RemoveDirectoryScript $target
            } else {
                Remove-Item -LiteralPath $target -Recurse -Force
            }
        } catch {
            Write-Warning "The active runtime is valid, but obsolete runtime cleanup failed for ${target}: $_"
        }
    }
}
