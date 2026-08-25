param(
    [Parameter(Mandatory = $true)]
    [string]$ModelRoot,

    [Parameter(Mandatory = $true)]
    [string]$IntegrityEvidence,

    [ValidateSet('local_low_gpu_nf4', 'local_integrity_nf4', 'high_gpu_bf16')]
    [string]$Profile = 'local_integrity_nf4',

    [string]$ReplayBundleRoot = '',

    [ValidateRange(1024, 65535)]
    [int]$Port = 8768
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
$inspectorScript = Join-Path $repositoryRoot 'scripts\run_req2web_inspector.py'
$resolvedModelRoot = (Resolve-Path -LiteralPath $ModelRoot).Path
$resolvedIntegrityEvidence = (Resolve-Path -LiteralPath $IntegrityEvidence).Path

if ([string]::IsNullOrWhiteSpace($ReplayBundleRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($env:REQ2WEB_LOCAL_DATA_ROOT)) {
        $localDataRoot = $env:REQ2WEB_LOCAL_DATA_ROOT
    }
    else {
        $localDataRoot = Join-Path (Split-Path -Parent $repositoryRoot) 'Req2Web_LocalData'
    }
    $ReplayBundleRoot = Join-Path $localDataRoot 'replay\inspector_replay_bundle_v1'
}
$resolvedReplayBundleRoot = (Resolve-Path -LiteralPath $ReplayBundleRoot).Path

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "The repository virtual environment is unavailable: $pythonPath"
}
if (-not (Test-Path -LiteralPath $resolvedModelRoot -PathType Container)) {
    throw "The model root is not a directory: $resolvedModelRoot"
}
if (-not (Test-Path -LiteralPath $resolvedIntegrityEvidence -PathType Leaf)) {
    throw "The model integrity evidence is not a file: $resolvedIntegrityEvidence"
}
if (-not (Test-Path -LiteralPath $resolvedReplayBundleRoot -PathType Container)) {
    throw "The Inspector replay bundle is not a directory: $resolvedReplayBundleRoot"
}

$arguments = @(
    $inspectorScript,
    '--bundle-root', $resolvedReplayBundleRoot,
    '--port', $Port,
    '--enable-local-semantic-assist',
    '--semantic-model-root', $resolvedModelRoot,
    '--semantic-integrity-evidence', $resolvedIntegrityEvidence,
    '--semantic-profile', $Profile,
    '--enable-local-canonical-run',
    '--local-model-root', $resolvedModelRoot,
    '--local-integrity-evidence', $resolvedIntegrityEvidence,
    '--canonical-profile', $Profile
)

Write-Host 'Checking the local Req2Web runtime without loading the model...'
& $pythonPath @arguments '--preflight-only'
if ($LASTEXITCODE -ne 0) {
    throw 'Req2Web startup preflight failed. Review the failed check above.'
}

Write-Host "Starting Req2Web Inspector at http://127.0.0.1:$Port/"
& $pythonPath @arguments
exit $LASTEXITCODE
