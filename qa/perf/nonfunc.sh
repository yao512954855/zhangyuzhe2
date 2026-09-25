#!/usr/bin/env bash
# ============================================================================
# QA-01 非功能验收驱动 —— 复用 qa/smoke/_bootstrap.sh 起一个真后端，
# 然后跑 nonfunc_core.py（P95 / 判分延迟 / WS 上限 / 安全）。
#
# 为什么复用 `_bootstrap.sh` 而不是自己写一份起停逻辑：
#   端口预检、UDP 9527 排他、临时库、Mock ASR、MSYS→Windows 路径转换、
#   进程树清理 —— 这些每一处都踩过坑。**只留一份**才不会漂。
#
# 跑法：
#     env -u PYTHONPATH bash qa/perf/nonfunc.sh
#     SMOKE_PORT=8821 NF_MIN_CHECKS=10 env -u PYTHONPATH bash qa/perf/nonfunc.sh
# ============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../smoke/_bootstrap.sh
source "$SELF_DIR/../smoke/_bootstrap.sh"

# QA-01 的非功能端口：与冒烟/e2e 默认的 8820 错开，方便两个窗口并行。
# ⚠️ 必须在 `qa_bootstrap_init` **之前**导出：init 里读的是 `${SMOKE_PORT:-8820}`
#    （本项目**没有**可配的 UDP 端口 —— 见 _bootstrap.sh 里那段注释）。
export SMOKE_PORT="${SMOKE_PORT:-8822}"
qa_bootstrap_init "QA-01 非功能"

qa_start_server

# qa_bootstrap_init 会把 SCRIPT_DIR 重设成调用方目录（= 本目录），可直接用。
# ⚠️ 脚本路径**必须**过 `qa_win`（`_bootstrap.sh` 提供）：
#    MSYS 的路径转换只对**可执行文件本身**的路径生效，**参数里的不认** ——
#    直接传 `/d/helloagent/.../nonfunc_core.py` 会被 Windows 版 python 解析成
#    `D:\d\helloagent\...` → `[Errno 2] No such file or directory`。
#    （首跑就踩了，正是 `qa/README.md` §四.2 记的第一条。）
"$PY" "$(qa_win "$SCRIPT_DIR/nonfunc_core.py")" \
  --base "$BASE" \
  --ws-base "$WS_BASE" \
  --min-checks "${NF_MIN_CHECKS:-10}"
rc=$?

qa_finish "$rc"
