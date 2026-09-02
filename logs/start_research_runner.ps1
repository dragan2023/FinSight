$ErrorActionPreference = 'Stop'
$projectRoot = 'E:/python_project/FinSight'
$p = Start-Process -FilePath (Join-Path $projectRoot '.venv/Scripts/python.exe') `
  -ArgumentList 'logs/live_research_run.py' `
  -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
Write-Output ('RUNNER_PID=' + $p.Id)
