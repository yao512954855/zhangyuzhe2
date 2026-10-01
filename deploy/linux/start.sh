#!/usr/bin/env bash
# ============================================================
# 背记系统 启动脚本
#   bash start.sh          # 后台启动，日志写入 backend/logs/server.log
#   bash start.sh 前台     # 前台运行（看日志排障用），Ctrl+C 退出
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/backend"
unset PYTHONPATH   # 防外部环境变量污染依赖解析

mkdir -p logs

PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2 || true)
PORT=${PORT:-8810}

if [ "${1:-}" = "前台" ]; then
  exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
fi

# ---------- 已在跑就不重复起 ----------
if [ -f run.pid ] && kill -0 "$(cat run.pid)" 2>/dev/null; then
  echo "[提示] 服务已在运行（PID $(cat run.pid)），端口 $PORT。要重启请先 bash stop.sh"
  exit 0
fi

echo "启动背记系统（端口 $PORT，UDP 发现服务 $((PORT+1117)) ）..."
nohup .venv/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 --port "$PORT" \
  >> logs/server.log 2>&1 &

PID=$!
echo "$PID" > run.pid

# ---------- 等待健康检查（最多 15 秒）----------
for _ in $(seq 1 15); do
  sleep 1
  if curl -sf -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo ""
    echo "=========================================="
    echo " 启动成功（PID $PID）"
    echo "   管理端  http://${IP:-本机IP}:$PORT/admin"
    echo "   健康检查 http://127.0.0.1:$PORT/api/health"
    echo "   日志    backend/logs/server.log"
    echo "=========================================="
    exit 0
  fi
  kill -0 "$PID" 2>/dev/null || { echo "[错误] 进程退出了，看日志：tail -50 backend/logs/server.log"; exit 1; }
done

echo "[警告] 15 秒内健康检查未通过，进程还在（PID $PID）。"
echo "       首次启动建库可能较慢，稍等后再试；或看日志：tail -50 backend/logs/server.log"
