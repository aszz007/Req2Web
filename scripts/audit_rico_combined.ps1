#requires -Version 5.1

[CmdletBinding()]
param(
    [int]$CandidateLimit = 180,
    [int]$Limit = 0
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$auditScript = Join-Path $PSScriptRoot "audit_rico_combined.py"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python environment not found: $python"
}
if (-not (Test-Path -LiteralPath $auditScript -PathType Leaf)) {
    throw "Audit script not found: $auditScript"
}

$arguments = @($auditScript, "--candidate-limit", $CandidateLimit)
if ($Limit -gt 0) {
    $arguments += @("--limit", $Limit)
}

& $python @arguments
exit $LASTEXITCODE
