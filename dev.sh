#!/usr/bin/env bash
# 一键启动：后端 FastAPI(8810) + 管理端 vite(5273)
# 用法：./dev.sh    退出：Ctrl+C（两个服务同时停止）
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"

# 已在运行则跳过对应服务
if curl -sf -m 2 http://127.0.0.1:8810/api/health >/dev/null 2>&1; then
    echo "[dev] 后端 8810 已在运行，跳过启动"
    BACK_PID=""
else
    mkdir -p "$ROOT/backend/logs"
    echo "[dev] 启动后端 8810 ...（日志: backend/logs/dev-8810.log）"
    (cd "$ROOT/backend" && exec .venv-linux/bin/python -m app.main >>logs/dev-8810.log 2>&1) &
    BACK_PID=$!
fi

if (exec 3<>/dev/tcp/127.0.0.1/5273) 2>/dev/null; then
    exec 3<&- 3>&-
    echo "[dev] 管理端 5273 已在运行，跳过启动"
    VITE_PID=""
else
    echo "[dev] 启动管理端 5273 ..."
    (cd "$ROOT/admin-web" && exec npm run dev) &
    VITE_PID=$!
fi

cleanup() {
    [ -n "$BACK_PID" ] && kill "$BACK_PID" 2>/dev/null
    [ -n "$VITE_PID" ] && kill "$VITE_PID" 2>/dev/null
    echo ""
    echo "[dev] 已停止"
}
trap cleanup EXIT INT TERM

echo "[dev] 就绪："
echo "[dev]   后端   http://192.168.0.153:8810/api/health"
echo "[dev]   管理端 http://192.168.0.153:5273/admin/"
wait
