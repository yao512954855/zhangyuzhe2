#!/usr/bin/env bash
# ============================================================================
# QA-01 功能矩阵**缺口补验**驱动 —— 起真后端，跑 gaps_core.py。
#
# 只补「查下来确实没有现成真机证据」的那几条（年级维度隔离 / 全字段落库 /
# 部署交付物 / 二期无实现），其余 40-规格 §5.1 的条目由各包产物覆盖，
# 在 `qa/reports/2026-09-25.md` 里逐条给出证据指针。
#
# 起停逻辑与 smoke/e2e/nonfunc **共用** `../smoke/_bootstrap.sh`（只留一份）。
#
# 跑法：
#     env -u PYTHONPATH bash qa/perf/gaps.sh
# ============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../smoke/_bootstrap.sh
source "$SELF_DIR/../smoke/_bootstrap.sh"

# 与 nonfunc（8822）、冒烟/e2e（8820）错开，方便并行做别的事
export SMOKE_PORT="${SMOKE_PORT:-8823}"
qa_bootstrap_init "QA-01 缺口补验"

qa_start_server

set +e
"$PY" "$(qa_win "$SCRIPT_DIR/gaps_core.py")" \
  --base "$BASE" \
  --db "$WIN_DB" \
  --min-checks "${GAP_MIN_CHECKS:-6}"
rc=$?
set -e

qa_finish "$rc"
