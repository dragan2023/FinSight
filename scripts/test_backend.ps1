$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$cacheRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot '.cache/pytest'))
$runDirectory = [IO.Path]::GetFullPath((Join-Path $cacheRoot ([Guid]::NewGuid().ToString())))
if (-not $runDirectory.StartsWith($cacheRoot + [IO.Path]::DirectorySeparatorChar)) {
    throw 'Test directory must stay inside the project cache.'
}
[IO.Directory]::CreateDirectory($cacheRoot) | Out-Null
$projectPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
Push-Location -LiteralPath $projectRoot
try {
    & $projectPython -m pytest tests -m 'not requires_api and not requires_llm' --basetemp $runDirectory -q
    $processExitCode = $LASTEXITCODE
}
finally { Pop-Location }
exit $processExitCode
