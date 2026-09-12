$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Test-FinSightBackend {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 3
        $health = $response.Content | ConvertFrom-Json
        return $health.app -eq 'FinSight' -and $health.status -eq 'ok'
    }
    catch { return $false }
}

function Test-FinSightFrontend {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5173/' -TimeoutSec 3
        return $response.Content -match '<title>FinSight'
    }
    catch { return $false }
}

function Get-ListeningPid([int]$Port) {
    $line = netstat -ano -p tcp | Where-Object {
        $_ -match "^\s*TCP\s+127\.0\.0\.1:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
    } | Select-Object -First 1
    if ($line -match 'LISTENING\s+(\d+)\s*$') { return [int]$Matches[1] }
    return $null
}

$targets = @(
    @{ Port = 8000; Name = 'backend'; Verified = (Test-FinSightBackend) },
    @{ Port = 5173; Name = 'frontend'; Verified = (Test-FinSightFrontend) }
)
$stopped = 0
foreach ($target in $targets) {
    if (-not $target.Verified) {
        Write-Host "$($target.Name): no verified FinSight service; skipped."
        continue
    }
    $listenerPid = Get-ListeningPid -Port $target.Port
    if ($null -eq $listenerPid) {
        Write-Warning "$($target.Name): verified response but no listening PID; not stopped."
        continue
    }
    try {
        Stop-Process -Id $listenerPid -Force -ErrorAction Stop
        Write-Host ('Stopped FinSight {0} (port {1}, PID {2}).' -f $target.Name, $target.Port, $listenerPid)
        $stopped += 1
    }
    catch {
        Write-Warning ('Cannot stop FinSight {0} (port {1}, PID {2}): {3}' -f $target.Name, $target.Port, $listenerPid, $_.Exception.Message)
    }
}
if ($stopped -eq 0) {
    Write-Host 'No service stopped: no process without a verified FinSight response is touched.'
    exit 1
}
Write-Host 'FinSight stopped. Double-click startFinSight.bat to start it again.'
