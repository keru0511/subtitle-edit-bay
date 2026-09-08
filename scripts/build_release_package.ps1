param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$')]
    [string]$ReleaseVersion,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$SourceSha,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [string]$ProducerRepository = $env:GITHUB_REPOSITORY,

    [long]$ProducerWorkflowRunId = 0,

    [int]$ProducerWorkflowRunAttempt = 0,

    [int]$PullRequestNumber = 0,

    [string]$PullRequestHeadSha = "",

    [string]$PullRequestBaseSha = "",

    [string]$PullRequestHeadBranch = "",

    [string]$SourceDirectory = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$appVersion = $ReleaseVersion.Substring(1)
$releaseDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$installerPath = Join-Path $releaseDirectory "SubtitleEditBay-Setup.exe"
New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null

& "$PSScriptRoot/build_installer.ps1" `
    -Version $appVersion `
    -OutputPath $installerPath `
    -ProjectRoot $SourceDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Installer build failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Installer build did not produce $installerPath."
}

$hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$runtimeContractHash = (Get-FileHash -LiteralPath (Join-Path $SourceDirectory "runtime/runtime-contract.json") -Algorithm SHA256).Hash.ToLowerInvariant()
$cpuLockHash = (Get-FileHash -LiteralPath (Join-Path $SourceDirectory "runtime/requirements-windows-cpu.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
$cudaLockHash = (Get-FileHash -LiteralPath (Join-Path $SourceDirectory "runtime/requirements-windows-cu128.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  SubtitleEditBay-Setup.exe" | Set-Content `
    -LiteralPath "$installerPath.sha256" `
    -Encoding ascii `
    -NoNewline
@{
    schema_version = 1
    package_type = "installer"
    app_version = $appVersion
    source_sha = $SourceSha.ToLowerInvariant()
    asset_name = "SubtitleEditBay-Setup.exe"
    sha256 = $hash
    required_files = @(
        "VERSION",
        "scripts/launch.ps1",
        "scripts/apply_installer_update.ps1",
        "scripts/setup.ps1",
        "scripts/validate_runtime.ps1",
        "scripts/runtime_activation.ps1",
        "scripts/runtime_contract.py",
        "runtime/runtime-contract.json",
        "runtime/requirements-windows-cpu.lock",
        "runtime/requirements-windows-cu128.lock"
    )
    runtime_contract = @{
        contract_sha256 = $runtimeContractHash
        cpu_lock_sha256 = $cpuLockHash
        cu128_lock_sha256 = $cudaLockHash
    }
} | ConvertTo-Json -Depth 5 | Set-Content `
    -LiteralPath "$installerPath.manifest.json" `
    -Encoding utf8
@{
    schema_version = 1
    source_sha = $SourceSha.ToLowerInvariant()
    release_version = $ReleaseVersion
    artifact_name = "subtitle-edit-bay-$appVersion-windows-installer-$($SourceSha.ToLowerInvariant())"
    asset_name = "SubtitleEditBay-Setup.exe"
    sha256 = $hash
    producer = @{
        repository = $ProducerRepository
        event_name = $env:GITHUB_EVENT_NAME
        pull_request_number = $PullRequestNumber
        pull_request_head_sha = $PullRequestHeadSha.ToLowerInvariant()
        pull_request_base_sha = $PullRequestBaseSha.ToLowerInvariant()
        pull_request_head_branch = $PullRequestHeadBranch
        workflow_path = ".github/workflows/release-readiness.yml"
        workflow_run_id = $ProducerWorkflowRunId
        workflow_run_attempt = $ProducerWorkflowRunAttempt
    }
} | ConvertTo-Json -Depth 5 | Set-Content `
    -LiteralPath (Join-Path $releaseDirectory "release-preparation.json") `
    -Encoding utf8
