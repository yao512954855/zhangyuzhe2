@echo off
REM ============================================================================
REM  背记系统 —— 手动备份（WP-BE-08 F8 / 需求 §11.4）
REM ============================================================================
REM  为什么要有这个 bat：
REM    服务每天 02:00 已经自动备份一次。但下面这些场景需要**马上**备一份：
REM      - 期末批量导入成绩/内容之前
REM      - 升级后端版本之前
REM      - 服务器要搬动/重装之前
REM    这时不可能去等凌晨两点，也不该让管理者登录管理端点按钮（可能登不上）。
REM    这个脚本**不依赖 HTTP 服务**（服务挂了也能备份），直接调后端服务层。
REM
REM  用法：双击，或在命令行执行
REM      ops\backup.bat
REM      ops\backup.bat D:\bjsys\backend          （显式指定后端目录）
REM
REM  产物：backend\backups\app_YYYYMMDD_HHMMSS.db（带秒，不会覆盖当天定时备份）
REM  保留：最近 30 份，超出的自动删最旧的
REM ============================================================================

setlocal
set "BACKEND_DIR=%~1"
if "%BACKEND_DIR%"=="" set "BACKEND_DIR=%~dp0..\backend"

if not exist "%BACKEND_DIR%\.env" (
    echo [X] 找不到配置文件 "%BACKEND_DIR%\.env"
    echo     请确认后端目录，或显式传入：ops\backup.bat D:\bjsys\backend
    exit /b 1
)

REM 优先用后端自带的虚拟环境解释器，保证依赖与线上一致
set "PY=%BACKEND_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ==^> 后端目录：%BACKEND_DIR%
echo ==^> Python  ：%PY%
echo.

pushd "%BACKEND_DIR%"
"%PY%" -c "import sys;from app.main import bootstrap;from app.services import backup_service as b;bootstrap();o=b.create_backup(daily=False,audit=True);d=o.to_dict();print('[OK] 备份完成：',d['file'],d['size_human'],'校验',d['integrity_ok'],'耗时',d['duration_ms'],'ms','保留',d['kept'],'份') if o.ok else print('[X] 备份失败：',d['error']);sys.exit(0 if o.ok else 2)"
set "RC=%ERRORLEVEL%"
popd

echo.
if "%RC%"=="0" (
    echo 提示：恢复步骤见 ops\README.md 第 6 节 —— **不要**在服务运行时替换数据库文件。
) else (
    echo 提示：排查见 ops\README.md 第 7 节。常见原因是磁盘满或备份目录无写权限。
)
endlocal & exit /b %RC%
