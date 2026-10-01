#!/usr/bin/env bash
# ============================================================
# 背记系统 Linux 一键安装脚本
#
# 用法（在发布包根目录）：
#   bash install.sh
#
# 做四件事：
#   1. 检查 Python 版本（>= 3.10，建议 3.12/3.13）
#   2. 建 Python 虚拟环境 backend/.venv
#   3. 装依赖（走清华镜像，需联网）
#   4. 校验配置文件（.env / asr.yaml）齐全
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

# ---------- 1. Python 检查 ----------
if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 找不到 python3，请先安装："
  echo "  Ubuntu/Debian: sudo apt install -y python3 python3-venv python3-pip"
  echo "  CentOS/RHEL:   sudo yum install -y python3 python3-pip"
  exit 1
fi

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
PYMIN=$(python3 -c 'import sys; print(f"{sys.version_info[0]*100+sys.version_info[1]}")')
echo "[1/4] Python 版本：$PYVER"
if [ "$PYMIN" -lt 310 ]; then
  echo "[错误] 需要 Python >= 3.10（依赖按 3.13 实测锁定），当前 $PYVER"
  exit 1
fi

# ---------- 2. 虚拟环境 ----------
echo "[2/4] 创建虚拟环境 backend/.venv ..."
if [ ! -d backend/.venv ]; then
  # Debian 系缺 venv 模块时给出明确指引
  if ! python3 -m venv backend/.venv 2>/tmp/_venv_err; then
    cat /tmp/_venv_err >&2
    echo ""
    echo "[错误] 创建虚拟环境失败，多半缺 python3-venv："
    echo "  Ubuntu/Debian: sudo apt install -y python3-venv"
    exit 1
  fi
else
  echo "      已存在，跳过"
fi

# ---------- 3. 依赖 ----------
echo "[3/4] 安装依赖（清华镜像，约 1-2 分钟）..."
backend/.venv/bin/pip install --upgrade pip -q \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
backend/.venv/bin/pip install -r backend/requirements.txt -q \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
echo "      依赖安装完成"

# ---------- 4. 配置校验 ----------
echo "[4/4] 校验配置文件..."
OK=1
if [ ! -f backend/.env ]; then
  echo "  [缺] backend/.env（服务密钥与管理员账号）"; OK=0
fi
if [ ! -f backend/asr.yaml ]; then
  echo "  [警告] backend/asr.yaml 不存在——服务能起，但语音识别不可用"
fi
if [ ! -f backend/static/admin/index.html ]; then
  echo "  [缺] backend/static/admin/（管理端页面）——发布包不完整！"; OK=0
fi
if [ "$OK" -ne 1 ]; then
  echo "[错误] 发布包不完整，联系打包的人。"
  exit 1
fi

echo ""
echo "=========================================="
echo " 安装完成！下一步："
echo "   bash start.sh        # 启动服务"
echo "   bash stop.sh         # 停止服务"
echo " 启动后浏览器访问："
echo "   管理端  http://本机IP:8810/admin"
echo "   接口    http://本机IP:8810/api/health"
echo "=========================================="
