$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Missing project .venv. Create it and install requirements.lock.txt first.'
}
Push-Location -LiteralPath $projectRoot
try {
    & $projectPython -m backend.app
    $processExitCode = $LASTEXITCODE
}
finally { Pop-Location }
exit $processExitCode
