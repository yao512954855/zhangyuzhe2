#!/usr/bin/env bash
# ============================================================
# 背记系统 Linux 服务 —— 注册 systemd 开机自启
#
# 用法（在发布包根目录，需要 sudo）：
#   sudo bash deploy/linux/install_service.sh
#   # 或脚本在 deploy/linux/ 下时：
#   sudo bash install_service.sh
#
# 做什么：
#   1. 定位发布包根目录（含 backend/ 的那层）与运行用户
#   2. 生成 /etc/systemd/system/bjsys-backend.service（路径按实际位置写死）
#   3. systemctl daemon-reload + enable --now（开机自启 + 立即拉起）
#
# 卸载：
#   sudo systemctl disable --now bjsys-backend
#   sudo rm /etc/systemd/system/bjsys-backend.service && sudo systemctl daemon-reload
#
# 为什么用 systemd 而不是 nohup/rc.local：
#   开机自启 + 崩溃自动重启（Restart=on-failure）+ journalctl 日志，一套全带。
# ============================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "[错误] 请用 sudo 运行（注册系统服务需要 root）"
  exit 1
fi

# ---------- 定位根目录：脚本可能在 <根>/deploy/linux/ 或 <根>/ ----------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/backend/app/main.py" ]; then
  ROOT="$SCRIPT_DIR"
else
  ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi
if [ ! -f "$ROOT/backend/app/main.py" ]; then
  echo "[错误] 定位不到 backend/（在 $ROOT 下没找到 backend/app/main.py）"
  exit 1
fi

if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  echo "[错误] $ROOT/backend/.venv 不存在 —— 先跑 bash install.sh 装好环境"
  exit 1
fi

# 运行用户：sudo 调用时取真实用户（root 直跑则用 root）
RUN_USER="${SUDO_USER:-root}"

UNIT=/etc/systemd/system/bjsys-backend.service

echo "[1/3] 写入 $UNIT（用户：$RUN_USER，目录：$ROOT/backend）"
cat > "$UNIT" <<EOF
[Unit]
Description=背记系统后端（FastAPI + uvicorn, 8810）
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${ROOT}/backend
ExecStart=${ROOT}/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8810
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

echo "[2/3] daemon-reload + enable --now（开机自启 + 立即启动）"
systemctl daemon-reload
systemctl enable --now bjsys-backend.service

echo "[3/3] 自检"
sleep 2
if systemctl is-active --quiet bjsys-backend.service; then
  echo "  服务已启动：$(systemctl is-active bjsys-backend.service)"
else
  echo "  [警告] 服务未起来，看日志：journalctl -u bjsys-backend -n 50 --no-pager"
  exit 1
fi

echo ""
echo "=========================================="
echo " 注册完成！开机自启已生效。常用命令："
echo "   systemctl status bjsys-backend     # 状态"
echo "   systemctl restart bjsys-backend    # 重启（改完 .env/asr.yaml 后）"
echo "   journalctl -u bjsys-backend -f     # 跟日志"
echo " 防火墙如开启需放行：TCP 8810 + UDP 9527"
echo "=========================================="
