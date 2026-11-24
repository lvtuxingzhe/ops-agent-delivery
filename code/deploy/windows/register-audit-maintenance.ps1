# 审计维护定时任务（Windows 任务计划版）
#
# 作用：每天在后台跑一次 `backend/scripts/audit_maintenance.py`
#       顺序固定「先补缓冲 → 再归档 → 最后校验」，默认归档「昨天」。
#
# 用法（在任意 PowerShell 窗口）：
#   .\register-audit-maintenance.ps1                 # 注册（每天 00:05）
#   .\register-audit-maintenance.ps1 -Time 00:20     # 改时间
#   .\register-audit-maintenance.ps1 -UseDocker      # 镜像部署，用 docker compose 执行
#   .\register-audit-maintenance.ps1 -Show           # 只看状态
#   .\register-audit-maintenance.ps1 -Unregister     # 撤销
#
# 源码部署读取 code\.env，镜像部署读取 .env.production；
# 换服务账号时需要单独配置该账号的权限。生产环境通常使用专用服务账号。

[CmdletBinding()]
param(
    [string]$TaskName = "OpsAgent-AuditMaintenance",
    [string]$Time = "00:05",
    [switch]$UseDocker,
    [switch]$Unregister,
    [switch]$Show
)

$ErrorActionPreference = "Stop"

# 代码根：本脚本在 code\deploy\windows\，往上两级就是 code\
$CodeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $CodeRoot "backend\.venv\Scripts\python.exe"
$Script = Join-Path $CodeRoot "backend\scripts\audit_maintenance.py"
$LogDir = Join-Path $CodeRoot "var\logs"
$LogFile = Join-Path $LogDir "audit-maintenance.log"

if ($Show) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { "未注册：$TaskName"; exit 0 }
    $task | Select-Object TaskName, State | Format-List
    (Get-ScheduledTaskInfo -TaskName $TaskName) | Select-Object LastRunTime, LastTaskResult, NextRunTime | Format-List
    exit 0
}

if ($Unregister) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { "未注册，无需撤销：$TaskName"; exit 0 }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    "已撤销：$TaskName"
    exit 0
}

if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$useDocker = $UseDocker -or -not (Test-Path -LiteralPath $Python)
if (-not $useDocker -and -not (Test-Path -LiteralPath $Script)) { throw "找不到：$Script" }
if ($useDocker) {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $dockerCommand) { throw "找不到 $Python，也没有 docker 命令" }
    $composeFile = Join-Path $CodeRoot "docker-compose.prod.yml"
    $envFile = Join-Path $CodeRoot ".env.production"
    foreach ($path in @($composeFile, $envFile)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "找不到：$path" }
    }
    $Inner = "cd '{0}'; & '{1}' compose --env-file .env.production -f docker-compose.prod.yml run --rm --no-deps -T backend python scripts/audit_maintenance.py *>> '{2}'" -f $CodeRoot, $dockerCommand.Source, $LogFile
} else {
    $Inner = "& '{0}' '{1}' *>> '{2}'" -f $Python, $Script, $LogFile
}
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ("-NoProfile -ExecutionPolicy Bypass -Command `"{0}`"" -f $Inner) -WorkingDirectory $CodeRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
# StartWhenAvailable：机器那天没开机，开机后补跑一次。
# 这一行保持单行：反引号续行在部分环境会被拆成独立命令。
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "运维 Agent 平台：审计缓冲补写 + 按天归档 + 校验（每天 $Time）" `
    -Force | Out-Null

"已注册：$TaskName（每天 $Time，日志 $LogFile）"
