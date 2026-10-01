#!/usr/bin/env bash
# ============================================================
# 背记系统 停止脚本：bash stop.sh
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/backend"

if [ ! -f run.pid ]; then
  echo "[提示] 没找到 run.pid，服务应该没在跑。"
  exit 0
fi

PID=$(cat run.pid)
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  # 最多等 10 秒优雅退出
  for _ in $(seq 1 10); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID"
    echo "已强制停止（PID $PID）"
  else
    echo "已停止（PID $PID）"
  fi
else
  echo "[提示] PID $PID 已不在运行，清理 pid 文件。"
fi
rm -f run.pid
