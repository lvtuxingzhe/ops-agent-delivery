$ErrorActionPreference = 'Stop'
$code = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$log = Join-Path $code 'var\logs\audit-maintenance.log'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Add-Content -LiteralPath $log -Value "=== $stamp ===" -Encoding UTF8
Set-Location -LiteralPath $code
& (Join-Path $code 'backend\.venv\Scripts\python.exe') (Join-Path $code 'backend\scripts\audit_maintenance.py') *>> $log
exit $LASTEXITCODE
