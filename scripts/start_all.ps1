param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$frontendRoot = Join-Path $projectRoot 'frontend'
$viteScript = Join-Path $frontendRoot 'node_modules\vite\bin\vite.js'
$logRoot = Join-Path $projectRoot 'logs'
$launcherLock = $null
$httpClient = $null
$started = @()
$exitCode = 0

function Test-FinSightProcess($process) {
    if (-not $process.CommandLine) { return $false }
    if ($process.Name -eq 'python.exe') {
        # Includes the Windows venv redirector's child, but not other Python jobs.
        $pattern = '^\s*"?' + [regex]::Escape($projectPython) + '"?\s+-m\s+backend\.app(?:\s|$)'
        return $process.CommandLine -match $pattern
    }
    if ($process.Name -eq 'node.exe') {
        # npm may use node_modules/.bin/../vite/bin/vite.js. Compare canonical paths.
        $arguments = [regex]::Matches($process.CommandLine, '"([^"]+)"|(\S+)')
        for ($index = 1; $index -lt $arguments.Count; $index++) {
            $match = $arguments[$index]
            $argument = if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
            if ($argument.StartsWith('-')) { continue }
            # Only the script argument counts; a later data argument must not establish ownership.
            try {
                # Node command lines can contain Chromium/V8 values that are
                # not legal Windows paths.  They are never this project's Vite
                # entry point, so ignore them instead of failing startup.
                if (-not [IO.Path]::IsPathRooted($argument)) { return $false }
                return [IO.Path]::GetFullPath($argument) -eq $viteScript
            }
            catch { return $false }
        }
    }
    return $false
}

function Stop-FinSightServices([int[]]$RootIds = @()) {
    try {
        $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'node.exe'" -ErrorAction Stop)
    }
    catch {
        # 受限账户下无法读到别的进程命令行时，退回到更窄的验证：只对本机
        # FinSight 健康检查/页面标题均明确匹配的监听端口执行终止。
        Write-Warning '无法读取现有 Python/Node 进程；改用 FinSight 端口响应验证。'
        & (Join-Path $PSScriptRoot 'stop_all.ps1')
        return
    }
    $owned = @($processes | Where-Object {
        (Test-FinSightProcess $_) -and
        ($RootIds.Count -eq 0 -or $_.ProcessId -in $RootIds -or $_.ParentProcessId -in $RootIds)
    } | Sort-Object CreationDate -Descending)
    foreach ($process in $owned) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ProcessId)"
        # Recheck identity immediately before stopping, including process creation time.
        if ($current -and $current.CreationDate -eq $process.CreationDate -and (Test-FinSightProcess $current)) {
            Write-Host "  关闭旧服务 PID $($process.ProcessId) ($($process.Name))"
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-FreePorts([int[]]$Ports) {
    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    do {
        $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        $occupied = @($Ports | Where-Object { $_ -in $listeners.Port })
        if ($occupied.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "端口 $($occupied -join ', ') 仍被占用。不会关闭无法确认属于本项目的程序，请手动处理或修改 .env 端口。"
}

function Read-LocalPage([string]$Url) {
    $response = $httpClient.GetAsync($Url).GetAwaiter().GetResult()
    try {
        if (-not $response.IsSuccessStatusCode) { throw 'HTTP not ready' }
        return $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    }
    finally { $response.Dispose() }
}

function Wait-Ready($Process, [string]$Url, [string]$Label, [switch]$Frontend) {
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        $Process.Refresh()
        if ($Process.HasExited) { throw "$Label 提前退出，请查看日志：$logRoot" }
        try {
            $health = (Read-LocalPage "$Url/health") | ConvertFrom-Json
            if ($health.app -eq 'FinSight' -and $health.status -eq 'ok') {
                if (-not $Frontend -or (Read-LocalPage "$Url/") -match '<div id="root"></div>') {
                    return
                }
            }
        }
        catch { }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Label 在 45 秒内未就绪，请查看日志：$logRoot"
}

try {
    Write-Host 'FinSight 一键启动' -ForegroundColor Green
    if (-not (Test-Path -LiteralPath $projectPython)) { throw '缺少项目 .venv，请先按 README 安装 Python 依赖。' }
    if (-not (Test-Path -LiteralPath $viteScript)) { throw '缺少前端依赖，请先在 frontend 目录执行 npm ci。' }
    $nodePath = (Get-Command node.exe -ErrorAction Stop).Source
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    try {
        $launcherLock = [IO.File]::Open((Join-Path $logRoot 'startup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    }
    catch { throw '已有启动流程正在运行，或 logs 目录不可写。请稍后重试。' }

    # Reuse backend dotenv parsing without printing any credential or validation input.
    $settingsCode = @'
import json, sys
try:
    import uvicorn
    from backend.app.settings import load_settings
    settings = load_settings()
    print(json.dumps(dict(backend=settings.port, frontend=settings.frontend_port)))
except Exception:
    sys.exit("FinSight configuration or Python dependencies are invalid; check README and .env.")
'@
    Push-Location -LiteralPath $projectRoot
    try {
        $configuration = $settingsCode | & $projectPython -
        if ($LASTEXITCODE -ne 0) { throw '配置或 Python 依赖检查失败，旧服务未关闭。' }
        $ports = $configuration | ConvertFrom-Json
    }
    finally { Pop-Location }
    if ($ports.backend -eq $ports.frontend) { throw '前后端端口不能相同，请修改 .env。' }

    Write-Host '[1/3] 关闭本项目旧服务...'
    Stop-FinSightServices
    Wait-FreePorts @($ports.backend, $ports.frontend)

    # Child-local overrides keep Vite's .env mode files and backend settings consistent.
    $env:FINSIGHT_HOST = '127.0.0.1'
    $env:FINSIGHT_PORT = [string]$ports.backend
    $env:FINSIGHT_FRONTEND_PORT = [string]$ports.frontend
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $httpClient = New-Object System.Net.Http.HttpClient($handler)
    $httpClient.Timeout = [TimeSpan]::FromSeconds(2)
    $webUrl = "http://127.0.0.1:$($ports.frontend)"

    Write-Host '[2/3] 启动后端与前端，等待健康检查...'
    $backend = Start-Process -FilePath $projectPython -ArgumentList '-m backend.app' -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'backend.stdout.log') `
        -RedirectStandardError (Join-Path $logRoot 'backend.stderr.log')
    $started += $backend.Id
    Wait-Ready $backend "http://127.0.0.1:$($ports.backend)" '后端'
    $frontend = Start-Process -FilePath $nodePath -ArgumentList ('"{0}"' -f $viteScript) -WorkingDirectory $frontendRoot `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'frontend.stdout.log') `
        -RedirectStandardError (Join-Path $logRoot 'frontend.stderr.log')
    $started += $frontend.Id
    Wait-Ready $frontend $webUrl '前端' -Frontend

    if ($NoBrowser) {
        Write-Host "[3/3] 服务已就绪（验证模式，不打开浏览器）：$webUrl" -ForegroundColor Green
    }
    else {
        Write-Host "[3/3] 使用系统默认浏览器打开 $webUrl" -ForegroundColor Green
        try { Start-Process -FilePath $webUrl | Out-Null }
        catch { Write-Warning "无法打开默认浏览器，服务仍在运行，请手动访问 $webUrl" }
    }
    Write-Host "日志目录：$logRoot"
    Write-Host '启动成功。关闭此窗口或浏览器不会停止服务，再次双击 BAT 会重启服务。'
}
catch {
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    if ($started.Count -gt 0) {
        try { Stop-FinSightServices -RootIds $started }
        catch { Write-Warning '清理本次启动的服务失败，请检查本机进程权限。' }
    }
    $exitCode = 1
}
finally {
    if ($httpClient) { $httpClient.Dispose() }
    if ($launcherLock) { $launcherLock.Dispose() }
}
exit $exitCode
