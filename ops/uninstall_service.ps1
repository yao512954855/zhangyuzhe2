# =============================================================================
# 背记系统 —— 卸载后端 Windows 服务
#                                WP-BE-08 F10 / 部署契约 §11
# =============================================================================
# 用法（**以管理员身份**打开 PowerShell）：
#
#   powershell -ExecutionPolicy Bypass -File ops\uninstall_service.ps1
#
# 只看看会做什么，不动手：
#
#   powershell -ExecutionPolicy Bypass -File ops\uninstall_service.ps1 -DryRun
#
# ⚠️ 本脚本**只卸载服务，不删任何数据**（data/ backups/ logs/ 一律保留）。
#    要彻底清理请手工处理 —— 那不该由卸载脚本替人做决定。
#
# 卸载流程（顺序不能颠倒）：
#   1) 停服务（先把 uvicorn 停掉，否则 data\*.db 被占用）
#   2) 等进程真正退出（Stop-Service 返回 ≠ 进程没了）
#   3) 删服务注册
# =============================================================================

[CmdletBinding()]
param(
    [string]$ServiceName = 'bjsys-backend',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Warn2([string]$Text) { Write-Host "[!] $Text" -ForegroundColor Yellow }
function Write-Ok([string]$Text) { Write-Host "[OK] $Text" -ForegroundColor Green }

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Warn2 "服务 $ServiceName 不存在，无需卸载。"
    exit 0
}

Write-Step "服务：$ServiceName（当前状态 $($svc.Status)）"

# --------------------------------------------------------------------- 1) 停

if ($svc.Status -ne 'Stopped') {
    if ($DryRun) {
        Write-Host "    [dry-run] Stop-Service $ServiceName -Force" -ForegroundColor DarkGray
    } else {
        Write-Step '停止服务'
        Stop-Service -Name $ServiceName -Force
    }
} else {
    Write-Ok '服务已停止'
}

# --------------------------------------------------------------------- 2) 等

# `Stop-Service` 返回时进程可能还在（SCM 说停了，Python 还在关连接池）。
# 直接删注册会在最后关头留下一个孤儿进程，端口照样占着，很难排查。
if (-not $DryRun) {
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Service -Name $ServiceName).Status -ne 'Stopped' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    $final = (Get-Service -Name $ServiceName).Status
    if ($final -ne 'Stopped') {
        Write-Warn2 "服务 30 秒内未完全停止（当前 $final）。可先 `taskkill /F` 掉残留 python 进程再重跑本脚本。"
        exit 3
    }
    Write-Ok '进程已退出'
}

# --------------------------------------------------------------------- 3) 删

if ($DryRun) {
    Write-Host "    [dry-run] sc.exe delete $ServiceName" -ForegroundColor DarkGray
    Write-Host "    [dry-run] （NSSM 注册的话等价于 nssm remove $ServiceName confirm）" -ForegroundColor DarkGray
    Write-Ok '[dry-run] 未做任何改动'
    exit 0
}

Write-Step '删除服务注册'
# `sc.exe delete` 对 NSSM 注册的服务同样有效 —— 它删的是 SCM 里的服务项，
# 不关心当初是谁注册的，所以不必区分 NSSM / New-Service 两条路径。
& sc.exe delete $ServiceName | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "sc.exe delete 返回 $LASTEXITCODE。若提示「标记为删除」属正常 —— SCM 会在句柄全部释放后完成删除，重启后即消失。"
}

Write-Host ''
Write-Ok "服务 $ServiceName 已卸载"
Write-Host ''
Write-Step '数据未受影响（按需自行处理）：'
Write-Host '  backend\data\       数据库（bjsys.db）'
Write-Host '  backend\backups\    备份文件'
Write-Host '  backend\logs\       运行日志'
Write-Host '  backend\.env        配置（含 JWT_SECRET / 初始管理员密码）'
