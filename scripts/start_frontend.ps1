$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot 'frontend'
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules/vite'))) {
    throw 'Missing frontend dependencies. Run npm ci in frontend first.'
}
Push-Location -LiteralPath $frontendRoot
try {
    & npm.cmd run dev
    $processExitCode = $LASTEXITCODE
}
finally { Pop-Location }
exit $processExitCode
