#!/usr/bin/env bash
# ============================================================================
# WP-QA-01 冒烟脚本 —— 一键跑通核心链路（真 uvicorn + 真库 + 真 HTTP/WebSocket）
#
# 《40-集成与验收规格》§2「冒烟清单」的可执行版本。**Mock ASR，不依赖云账号**，
# 所以本地随时可跑、CI 也能跑。
#
#   bash qa/smoke/smoke.sh
#
# 断言全在 `smoke_core.py`（本文件只管起停与汇总）；起停逻辑与 `qa/e2e/e2e.sh`
# 共用 `_bootstrap.sh`，**刻意不各写一份** —— 否则「冒烟过、e2e 挂」的排查成本极高。
#
# 可选环境变量：SMOKE_PORT=8820（默认） / KEEP_TMP=1（失败时保留临时目录）
#
# ⚠️ 两个必须知道的坑（详见 _bootstrap.sh 的注释）：
#   1) `unset PYTHONPATH`（沙箱 safe-delete shim 会用 SystemExit(1) 杀掉 uvicorn）；
#   2) UDP 9527 必须空闲，否则发现应答会打到别的进程上 → **假绿**，故直接 exit 2。
# ============================================================================
set -uo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_bootstrap.sh"
qa_bootstrap_init "QA-01 冒烟"
qa_start_server

set +e
# 路径都走 Windows 形式（`qa_win`）—— 参数里的 MSYS 路径不会自动转换，
# 见 `_bootstrap.sh` 里 `qa_win` 上方的注释（真踩过：脚本路径变 D:\d\...、库文件两边不一致）。
"$PY" "$(qa_win "$SCRIPT_DIR/smoke_core.py")" \
  --base-url "$BASE" \
  --ws-url "$WS_BASE" \
  --db "$WIN_DB" \
  --backups "$WIN_BACKUPS"
RC=$?
set -e

qa_finish "$RC"
