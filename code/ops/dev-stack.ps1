# Ops Agent 本地开发栈守护脚本
#
# 由 Windows 计划任务在登录时启动，不挂在对话/终端会话的进程树下。
# 职责：
#   1. 等 Docker 可用，确保 PostgreSQL / Redis 容器在跑；
#   2. 保持后端 127.0.0.1:8000、前端 127.0.0.1:5173、队列 Worker 在线；
#   3. 按端口/进程健康状态自动重启，不依赖虚拟环境启动器的 PID；
#   4. 日志写 var/tmp。
#
# 手动排查时也可以直接执行本脚本；按 Ctrl+C 结束守护，不会删除数据。

$ErrorActionPreference = 'Stop'
$Root = 'D:\workbudy\work\ops-agent-delivery\code'
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$Python = Join-Path $BackendDir '.venv\Scripts\python.exe'
$Node = 'C:\Program Files\nodejs\node.exe'
$Vite = Join-Path $FrontendDir 'node_modules\vite\bin\vite.js'
$Docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$LogDir = Join-Path $Root 'var\tmp'
$LogFile = Join-Path $LogDir 'dev-stack.log'

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Write-StackLog {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
}

function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    return ($null -ne $conn)
}

function Wait-DockerReady {
    param([int]$TimeoutSeconds = 150)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $ready = $false
        $oldEap = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'SilentlyContinue'
            $global:LASTEXITCODE = 1
            & $Docker info *> $null
            $ready = ($LASTEXITCODE -eq 0)
        } catch {
            $ready = $false
        } finally {
            $ErrorActionPreference = $oldEap
        }
        if ($ready) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Start-Containers {
    $oldEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        $global:LASTEXITCODE = 1
        & $Docker start ops-postgres-15 ops-redis *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $oldEap
    }
}

function Start-Backend {
    $stamp = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), (Get-Random -Minimum 1000 -Maximum 9999)
    $out = Join-Path $LogDir ("backend-dev-{0}.out.log" -f $stamp)
    $err = Join-Path $LogDir ("backend-dev-{0}.err.log" -f $stamp)
    $p = Start-Process -FilePath $Python `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') `
        -WorkingDirectory $BackendDir -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    Write-StackLog ('后端已拉起，PID {0}' -f $p.Id)
}

function Start-Frontend {
    $stamp = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), (Get-Random -Minimum 1000 -Maximum 9999)
    $out = Join-Path $LogDir ("frontend-dev-{0}.out.log" -f $stamp)
    $err = Join-Path $LogDir ("frontend-dev-{0}.err.log" -f $stamp)
    $p = Start-Process -FilePath $Node `
        -ArgumentList @($Vite, '--host', '127.0.0.1', '--port', '5173') `
        -WorkingDirectory $FrontendDir -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    Write-StackLog ('前端已拉起，PID {0}' -f $p.Id)
}

function Start-Worker {
    $stamp = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), (Get-Random -Minimum 1000 -Maximum 9999)
    $out = Join-Path $LogDir ("worker-dev-{0}.out.log" -f $stamp)
    $err = Join-Path $LogDir ("worker-dev-{0}.err.log" -f $stamp)
    $p = Start-Process -FilePath $Python `
        -ArgumentList @('backend\scripts\run_queue_worker.py', '--interval', '5') `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    Write-StackLog ('Worker 已拉起，PID {0}' -f $p.Id)
}

function Test-WorkerRunning {
    $items = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*run_queue_worker.py*' }
    return ($null -ne $items)
}

try {
    Write-StackLog '守护启动'

    if (Wait-DockerReady) {
        if (Start-Containers) {
            Write-StackLog 'PostgreSQL / Redis 容器已就绪'
        } else {
            Write-StackLog '容器启动命令返回非 0，稍后重试'
        }
    } else {
        Write-StackLog '等待 Docker 超时；先启动后端，数据库恢复后会自动重连'
    }

    $backendLastStart = [datetime]::MinValue
    $frontendLastStart = [datetime]::MinValue
    $workerLastStart = [datetime]::MinValue
    $backendUp = $false
    $frontendUp = $false
    $workerUp = $false
    $tick = 0

    while ($true) {
        $tick += 1
        $backendNow = Test-PortListening 8000
        $frontendNow = Test-PortListening 5173
        $workerNow = if (($tick % 3) -eq 1) { Test-WorkerRunning } else { $workerUp }

        if ($backendNow -and -not $backendUp) {
            Write-StackLog '后端 8000 在线'
            $backendUp = $true
        }
        if (-not $backendNow) {
            $backendUp = $false
            if (((Get-Date) - $backendLastStart).TotalSeconds -ge 20) {
                try {
                    Start-Backend
                    $backendLastStart = Get-Date
                    Write-StackLog '后端未监听，已发起启动'
                } catch {
                    Write-StackLog ('后端启动失败：{0}' -f $_.Exception.Message)
                }
            }
        }

        if ($frontendNow -and -not $frontendUp) {
            Write-StackLog '前端 5173 在线'
            $frontendUp = $true
        }
        if (-not $frontendNow) {
            $frontendUp = $false
            if (((Get-Date) - $frontendLastStart).TotalSeconds -ge 20) {
                try {
                    Start-Frontend
                    $frontendLastStart = Get-Date
                    Write-StackLog '前端未监听，已发起启动'
                } catch {
                    Write-StackLog ('前端启动失败：{0}' -f $_.Exception.Message)
                }
            }
        }

        if ($workerNow -and -not $workerUp) {
            Write-StackLog 'Worker 在线'
            $workerUp = $true
        }
        if (-not $workerNow) {
            $workerUp = $false
            if (((Get-Date) - $workerLastStart).TotalSeconds -ge 20) {
                try {
                    Start-Worker
                    $workerLastStart = Get-Date
                    Write-StackLog 'Worker 未运行，已发起启动'
                } catch {
                    Write-StackLog ('Worker 启动失败：{0}' -f $_.Exception.Message)
                }
            }
        }

        if (($tick % 60) -eq 0) {
            [void](Start-Containers)
        }

        Start-Sleep -Seconds 5
    }
} catch {
    Write-StackLog ('守护异常退出：{0}' -f $_.Exception.Message)
    exit 1
}
