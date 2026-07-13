#requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$NoManifestUpdate
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$auditScript = Join-Path $PSScriptRoot "audit_vision2web.py"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python environment not found. Run: py -3.12 -m venv .venv"
}
if (-not (Test-Path -LiteralPath $auditScript -PathType Leaf)) {
    throw "Audit script not found: $auditScript"
}

$arguments = @($auditScript)
if ($NoManifestUpdate) {
    $arguments += "--no-manifest-update"
}

& $python @arguments
exit $LASTEXITCODE
