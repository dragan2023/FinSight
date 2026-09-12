$ErrorActionPreference = 'Stop'
$stopScript = Join-Path $PSScriptRoot 'stop_all.ps1'

try {
    $process = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $stopScript
    )
    exit $process.ExitCode
}
catch {
    Write-Host 'Administrator confirmation was not completed; FinSight was not stopped.'
    exit 1
}
