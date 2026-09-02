$ErrorActionPreference = 'Stop'
$projectRoot = 'E:/python_project/FinSight'
$p = Start-Process -FilePath (Join-Path $projectRoot '.venv/Scripts/python.exe') `
  -ArgumentList '-m','backend.app' `
  -WorkingDirectory $projectRoot -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $projectRoot 'logs/backend_live.out.log') `
  -RedirectStandardError (Join-Path $projectRoot 'logs/backend_live.err.log') `
  -PassThru
Write-Output ('PID=' + $p.Id)
