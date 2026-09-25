# =============================================================================
# 背记系统 —— 注册为 Windows 服务（开机自启）
#                                WP-BE-08 F10 / 部署契约 §11
# =============================================================================
# 用法（**以管理员身份**打开 PowerShell）：
#
#   powershell -ExecutionPolicy Bypass -File ops\install_service.ps1
#
# 先干跑看看会做什么（不产生任何改动，普通权限即可）：
#
#   powershell -ExecutionPolicy Bypass -File ops\install_service.ps1 -DryRun
#
# 卸载：
#
#   powershell -ExecutionPolicy Bypass -File ops\uninstall_service.ps1
#
# 两个后端二选一（脚本自动嗅探，也可用 -PythonPath 显式指定）：
#   1) 装了 NSSM      → 用 NSSM，**带崩溃自动重启**（推荐，部署手册 §5）
#   2) 没装 NSSM      → 退回 Windows 自带 `New-Service`，能自启但**不自动重启**
#
# 为什么不用「任务计划程序」：它默认在用户登录后才跑，而服务器要的是
# 「开机就起、不登录也起」—— 那是服务的语义，不是计划任务的语义。
# =============================================================================

[CmdletBinding()]
param(
    [string]$ServiceName = 'bjsys-backend',
    [string]$DisplayName = '背记系统 后端服务',
    [string]$Description = '中小学英语单词 / 语文课文背诵系统 —— 后端 API（FastAPI + uvicorn）',

    # 默认取脚本所在目录的上一级的 backend/
    [string]$BackendDir = '',
    # 留空则自动嗅探 backend\.venv\Scripts\python.exe
    [string]$PythonPath = '',

    [int]$Port = 8810,
    [string]$HostAddress = '0.0.0.0',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Warn2([string]$Text) { Write-Host "[!] $Text" -ForegroundColor Yellow }
function Write-Ok([string]$Text) { Write-Host "[OK] $Text" -ForegroundColor Green }

# ----------------------------------------------------------------------------- 参数解析

if ([string]::IsNullOrWhiteSpace($BackendDir)) {
    $BackendDir = (Resolve-Path (Join-Path $PSScriptRoot '..\backend')).Path
}
if (-not (Test-Path -LiteralPath $BackendDir)) {
    throw "后端目录不存在：$BackendDir"
}
$BackendDir = (Resolve-Path -LiteralPath $BackendDir).Path

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $candidate = Join-Path $BackendDir '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate) {
        $PythonPath = $candidate
    } else {
        $PythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    }
}
if ([string]::IsNullOrWhiteSpace($PythonPath) -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "找不到 Python 解释器。请用 -PythonPath 指定，例如 -PythonPath D:\bjsys\backend\.venv\Scripts\python.exe"
}

$envFile = Join-Path $BackendDir '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "缺少配置文件 $envFile —— 先 copy .env.example .env 并改好 JWT_SECRET 与 ADMIN_INIT_PASSWORD（后端会拒绝启动）"
}

# 数据库/logs/backups 的路径以 backend 为基准，所以目录先建好
foreach ($sub in @('data', 'logs', 'backups')) {
    $p = Join-Path $BackendDir $sub
    if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

# ----------------------------------------------------------------------------- 环境检查

Write-Step "后端目录：$BackendDir"
Write-Step "Python   ：$PythonPath"

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Warn2 "服务 $ServiceName 已存在（状态：$($existing.Status)）。请先运行 uninstall_service.ps1，或用 -ServiceName 换个名字。"
    exit 2
}

# 端口占用预检：被别的程序占着的话，注册完了也起不来，白折腾
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Warn2 "TCP $Port 已被占用（PID $($busy[0].OwningProcess)）。部署契约 §11 约定用 8810，请先释放该端口。"
} else {
    Write-Ok "TCP $Port 空闲"
}

# 迁移 + 自检先跑一遍：把「配置错/库坏了」这类问题在装服务之前暴露出来
Write-Step "预检：执行迁移与启动自检（不启动 HTTP 服务）"
$precheck = @'
import sys, json
from app.main import bootstrap
from app.tasks.scheduler import run_startup_checks
bootstrap()
results = [r.to_dict() for r in run_startup_checks()]
print(json.dumps(results, ensure_ascii=False))
sys.exit(0 if all(r["ok"] for r in results) else 1)
'@
if ($DryRun) {
    Write-Host "    [dry-run] 将执行：`n$precheck" -ForegroundColor DarkGray
} else {
    $precheckFile = Join-Path $env:TEMP 'bjsys_precheck.py'
    Set-Content -LiteralPath $precheckFile -Value $precheck -Encoding UTF8
    Push-Location $BackendDir
    try {
        & $PythonPath $precheckFile
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 "自检有未通过项（上面已列出）。仍会继续注册服务，但请先按 ops\README.md §7 排掉。"
        } else {
            Write-Ok "自检全部通过"
        }
    } finally {
        Pop-Location
        Remove-Item -LiteralPath $precheckFile -Force -ErrorAction SilentlyContinue
    }
}

# ----------------------------------------------------------------------------- 选择注册方式

$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssm) {
    $local = Join-Path $PSScriptRoot 'nssm.exe'
    if (Test-Path -LiteralPath $local) { $nssm = @{ Source = $local } }
}

$appArgs = @('-m', 'uvicorn', '--app-dir', $BackendDir, 'app.main:app', '--host', $HostAddress, '--port', "$Port")

if ($nssm) {
    $nssmExe = $nssm.Source
    Write-Step "使用 NSSM 注册（带崩溃自动重启）：$nssmExe"

    if ($DryRun) {
        Write-Host "    [dry-run] nssm install $ServiceName `"$PythonPath`" $($appArgs -join ' ')" -ForegroundColor DarkGray
        Write-Host "    [dry-run] nssm set $ServiceName AppDirectory `"$BackendDir`"" -ForegroundColor DarkGray
        Write-Host "    [dry-run] nssm set $ServiceName AppStdout/AppStderr -> $BackendDir\logs\service.log" -ForegroundColor DarkGray
        Write-Host "    [dry-run] nssm set $ServiceName Start SERVICE_AUTO_START" -ForegroundColor DarkGray
        Write-Host "    [dry-run] nssm set $ServiceName AppExit Default Restart（崩溃后 5 秒重起）" -ForegroundColor DarkGray
        Write-Host "    [dry-run] nssm start $ServiceName" -ForegroundColor DarkGray
    } else {
        & $nssmExe install $ServiceName $PythonPath @appArgs | Out-Null
        & $nssmExe set $ServiceName AppDirectory $BackendDir | Out-Null
        & $nssmExe set $ServiceName DisplayName $DisplayName | Out-Null
        & $nssmExe set $ServiceName Description $Description | Out-Null
        & $nssmExe set $ServiceName AppStdout (Join-Path $BackendDir 'logs\service.log') | Out-Null
        & $nssmExe set $ServiceName AppStderr (Join-Path $BackendDir 'logs\service.err.log') | Out-Null
        & $nssmExe set $ServiceName AppRotateFiles 1 | Out-Null
        & $nssmExe set $ServiceName Start SERVICE_AUTO_START | Out-Null
        # 崩溃自动重启：校园网环境下「服务悄悄挂掉没人发现」是最大的运维风险
        & $nssmExe set $ServiceName AppExit Default Restart | Out-Null
        & $nssmExe set $ServiceName AppRestartDelay 5000 | Out-Null
        & $nssmExe start $ServiceName | Out-Null
        Write-Ok "服务已注册并启动：$ServiceName"
    }
} else {
    Write-Warn2 "未找到 nssm。退回 Windows 自带 New-Service（能开机自启，但**不会**崩溃自动重启）。"
    Write-Warn2 "强烈建议装 NSSM 后重跑本脚本：https://nssm.cc/download （把 nssm.exe 放到 ops\ 目录即可被自动嗅探）"

    # 用 -EncodedCommand 不行（太长），直接把命令行交给 SCM
    $binary = '"{0}" {1}' -f $PythonPath, ($appArgs -join ' ')
    if ($DryRun) {
        Write-Host "    [dry-run] New-Service -Name $ServiceName -BinaryPathName $binary -StartupType Automatic" -ForegroundColor DarkGray
        Write-Host "    [dry-run] sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/5000/restart/5000" -ForegroundColor DarkGray
        Write-Host "    [dry-run] Start-Service $ServiceName" -ForegroundColor DarkGray
    } else {
        New-Service -Name $ServiceName -BinaryPathName $binary -DisplayName $DisplayName `
            -Description $Description -StartupType Automatic | Out-Null
        # `New-Service` 不支持失败恢复策略，只能补一条 sc.exe（这是它比 NSSM 弱的地方）
        & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/5000/restart/5000 | Out-Null
        Start-Service -Name $ServiceName
        Write-Ok "服务已注册并启动：$ServiceName"
    }
}

# ----------------------------------------------------------------------------- 收尾提示

Write-Host ''
Write-Step '下一步（部署手册 §2~§4）'
Write-Host '  1) 放行防火墙：'
Write-Host "       netsh advfirewall firewall add rule name=`"bjsys-api`" dir=in action=allow protocol=TCP localport=$Port"
Write-Host '       netsh advfirewall firewall add rule name="bjsys-discovery" dir=in action=allow protocol=UDP localport=9527'
Write-Host '  2) 路由器上把本机 MAC 绑定固定 IP（否则 App 每次都要重新扫码）'
Write-Host "  3) 验证：Invoke-RestMethod http://127.0.0.1:$Port/api/ping"
Write-Host '  4) 管理端打开：'
Write-Host "       http://<本机局域网IP>:$Port/admin"
Write-Host '  完整说明见 ops\README.md'
