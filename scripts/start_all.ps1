# start_all.ps1 — 一键启动 Harness Framework 全套服务 (Windows PowerShell)
#
# 使用方式：
#   .\scripts\start_all.ps1           # 启动全部服务
#   .\scripts\start_all.ps1 -ConsulOnly    # 仅启动 Consul
#   .\scripts\start_all.ps1 -DaemonOnly    # 仅启动 harness_framework
#   .\scripts\start_all.ps1 -DashboardOnly # 仅启动 agent_dashboard
#   .\scripts\start_all.ps1 -Stop          # 停止所有服务
#   .\scripts\start_all.ps1 -Status         # 查看状态

param(
    [switch]$ConsulOnly,
    [switch]$DaemonOnly,
    [switch]$DashboardOnly,
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 配置
$ProjectDir = Split-Path -Parent $PSScriptRoot
$ConsulDir = Join-Path $ProjectDir "consul_server"
$DashboardDir = Join-Path $ProjectDir "agent_dashboard"
$LogDir = Join-Path $env:TEMP "harness-framework"
$PidDir = Join-Path $LogDir "pids"
$ConsulPort = if ($env:CONSUL_PORT) { $env:CONSUL_PORT } else { 8500 }
$DaemonPort = if ($env:DAEMON_PORT) { $env:DAEMON_PORT } else { 8080 }
$DashboardPort = if ($env:DASHBOARD_PORT) { $env:DASHBOARD_PORT } else { 3000 }

# 颜色函数
function Write-Info { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }

# 创建目录
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $PidDir | Out-Null

# 检查端口是否被占用
function Test-PortFree {
    param($Port)
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse("127.0.0.1"), $Port)
    try {
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

# 等待服务就绪
function Wait-ForService {
    param($Url, $MaxRetries = 10, $Interval = 500)
    for ($i = 0; $i -lt $MaxRetries; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
                return $true
            }
        } catch { }
        Start-Sleep -Milliseconds $Interval
    }
    return $false
}

# 启动 Consul
function Start-Consul {
    if (-not (Test-PortFree $ConsulPort)) {
        Write-Warn "Consul 已在端口 $ConsulPort 运行"
        return
    }

    Write-Info "启动 Consul..."

    $configFile = Join-Path $LogDir "consul-cors.hcl"
    @"
http_config {
  response_headers {
    "Access-Control-Allow-Origin"  = "*"
    "Access-Control-Allow-Methods" = "GET, POST, PUT, DELETE, OPTIONS"
    "Access-Control-Allow-Headers" = "Content-Type, X-Consul-Token, X-Consul-Index"
    "Access-Control-Expose-Headers" = "X-Consul-Index, X-Consul-Knownleader, X-Consul-Lastcontact"
  }
}
"@ | Out-File -FilePath $configFile -Encoding UTF8

    $consulBin = Join-Path $ConsulDir "consul.exe"
    if (-not (Test-Path $consulBin)) {
        $consulBin = "consul.exe"
    }

    $logFile = Join-Path $LogDir "consul.log"
    $proc = Start-Process -FilePath $consulBin `
        -ArgumentList "agent", "-dev", "-client=0.0.0.0", "-data-dir=`"$LogDir\consul-data`"", "-config-file=`"$configFile`"" `
        -NoNewWindow -PassThru -RedirectStandardOutput $logFile -RedirectStandardError $logFile

    $proc | Out-Null
    $proc.Id | Out-File (Join-Path $PidDir "consul.pid")

    if (Wait-ForService "http://127.0.0.1:$ConsulPort/v1/status/leader") {
        Write-Info "Consul 已启动 (PID: $($proc.Id), 端口: $ConsulPort)"
    } else {
        Write-Err "Consul 启动失败，查看日志: $logFile"
        exit 1
    }
}

# 启动 harness_framework daemon
function Start-Daemon {
    if (-not (Test-PortFree $DaemonPort)) {
        Write-Warn "harness_framework 已在端口 $DaemonPort 运行"
        return
    }

    Write-Info "启动 harness_framework daemon..."

    $logFile = Join-Path $LogDir "daemon.log"
    $proc = Start-Process -FilePath "python" `
        -ArgumentList "-m", "harness_framework.daemon", "--port", $DaemonPort, "--consul", "127.0.0.1:$ConsulPort" `
        -NoNewWindow -PassThru -WorkingDirectory $ProjectDir `
        -RedirectStandardOutput $logFile -RedirectStandardError $logFile

    $proc | Out-Null
    $proc.Id | Out-File (Join-Path $PidDir "daemon.pid")

    if (Wait-ForService "http://127.0.0.1:$DaemonPort/api/workflows") {
        Write-Info "harness_framework 已启动 (PID: $($proc.Id), 端口: $DaemonPort)"
    } else {
        Write-Err "harness_framework 启动失败，查看日志: $logFile"
        exit 1
    }
}

# 启动 agent_dashboard
function Start-Dashboard {
    if (-not (Test-PortFree $DashboardPort)) {
        Write-Warn "agent_dashboard 已在端口 $DashboardPort 运行"
        return
    }

    Write-Info "启动 agent_dashboard..."

    # 检查 node_modules
    $nodeModules = Join-Path $DashboardDir "node_modules"
    if (-not (Test-Path $nodeModules)) {
        Write-Info "安装依赖..."
        Push-Location $DashboardDir
        npm install --silent 2>&1 | Out-Null
        Pop-Location
    }

    $logFile = Join-Path $LogDir "dashboard.log"
    $env:PORT = $DashboardPort
    $proc = Start-Process -FilePath "npm" `
        -ArgumentList "run", "dev" `
        -NoNewWindow -PassThru -WorkingDirectory $DashboardDir `
        -RedirectStandardOutput $logFile -RedirectStandardError $logFile

    $proc | Out-Null
    $proc.Id | Out-File (Join-Path $PidDir "dashboard.pid")

    if (Wait-ForService "http://127.0.0.1:$DashboardPort" -MaxRetries 30 -Interval 1000) {
        Write-Info "agent_dashboard 已启动 (PID: $($proc.Id), 端口: $DashboardPort)"
    } else {
        Write-Err "agent_dashboard 启动失败，查看日志: $logFile"
        exit 1
    }
}

# 停止所有服务
function Stop-All {
    Write-Info "停止所有服务..."

    Get-ChildItem $PidDir -Filter "*.pid" | ForEach-Object {
        $name = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
        $pid = Get-Content $_.FullName -ErrorAction SilentlyContinue
        if ($pid -and (Get-Process -Id $pid -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            Write-Info "已停止 $name (PID: $pid)"
        }
        Remove-Item $_.FullName -Force
    }

    # 停止可能的遗留进程
    Get-Process | Where-Object {
        $_.Name -match "daemon|harness|consul|vite|node" -and
        $_.MainWindowTitle -eq ""
    } | ForEach-Object {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }

    Write-Info "所有服务已停止"
}

# 状态检查
function Show-Status {
    Write-Info "服务状态："

    Write-Host -NoNewline "  Consul ($ConsulPort): "
    if (Wait-ForService "http://127.0.0.1:$ConsulPort/v1/status/leader" -MaxRetries 2 -Interval 500) {
        Write-Host "运行中" -ForegroundColor Green
    } else {
        Write-Host "未运行" -ForegroundColor Red
    }

    Write-Host -NoNewline "  harness_framework ($DaemonPort): "
    if (Wait-ForService "http://127.0.0.1:$DaemonPort/api/workflows" -MaxRetries 2 -Interval 500) {
        Write-Host "运行中" -ForegroundColor Green
    } else {
        Write-Host "未运行" -ForegroundColor Red
    }

    Write-Host -NoNewline "  agent_dashboard ($DashboardPort): "
    if (Wait-ForService "http://127.0.0.1:$DashboardPort" -MaxRetries 2 -Interval 500) {
        Write-Host "运行中" -ForegroundColor Green
    } else {
        Write-Host "未运行" -ForegroundColor Red
    }
}

# 打印访问地址
function Print-Urls {
    Write-Host ""
    Write-Info "访问地址："
    Write-Host "  Consul UI:    http://localhost:$ConsulPort/ui"
    Write-Host "  API:          http://localhost:$DaemonPort"
    Write-Host "  Dashboard:    http://localhost:$DashboardPort"
    Write-Host ""
}

# 主流程
function Main {
    if ($Stop) {
        Stop-All
        return
    }

    if ($Status) {
        Show-Status
        return
    }

    if ($ConsulOnly) {
        Start-Consul
        Print-Urls
        return
    }

    if ($DaemonOnly) {
        Start-Consul
        Start-Daemon
        Print-Urls
        return
    }

    if ($DashboardOnly) {
        Start-Dashboard
        Print-Urls
        return
    }

    # 启动全部服务
    Start-Consul
    Start-Daemon
    Start-Dashboard
    Print-Urls
    Write-Info "按 Ctrl+C 停止所有服务"

    # 等待中断
    try {
        while ($true) { Start-Sleep 1 }
    } finally {
        Stop-All
    }
}

Main
