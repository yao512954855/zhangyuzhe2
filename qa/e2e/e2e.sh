#!/usr/bin/env bash
# ============================================================================
# WP-QA-01 端到端场景 —— 《40-集成与验收规格》§3
#
#   bash qa/e2e/e2e.sh
#
# 覆盖 E2E-04 / E2E-08 / E2E-09 / E2E-10，并**真跑一遍 E2E-06**（停服 → 换库 → 启服）。
# 其余场景（E2E-01/02/03/05/07）由各包真机烟测覆盖，映射表写在 `e2e_core.py` 的文件头里 ——
# 重复实现只会多一份会漂的代码。
#
# E2E-06 为什么要在 shell 里做：它必须真的把服务停掉、用备份文件替换数据库、
# 再启起来 —— 进程归 shell 管，驱动进程做不到。所以拆成
# `restore-prepare`（记备份点）→ shell 停服/换库/启服 → `restore-verify`（断言回到备份点）。
#
# 起停逻辑与 `qa/smoke/smoke.sh` **共用** `../smoke/_bootstrap.sh`。
# 可选环境变量：SMOKE_PORT=8820（默认） / KEEP_TMP=1
# ============================================================================
set -uo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../smoke" && pwd)/_bootstrap.sh"
qa_bootstrap_init "QA-01 端到端"
qa_start_server

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECT_FILE="$TMP/restore_expect.json"
# 交给 python 的一律用 Windows 路径（原因见 _bootstrap.sh 的 qa_win 注释）
E2E_CORE_W="$(qa_win "$E2E_DIR/e2e_core.py")"
EXPECT_FILE_W="$(qa_win "$EXPECT_FILE")"
RC=0

run_mode() { # $1=mode $2=min-checks
  "$PY" "$E2E_CORE_W" \
    --mode "$1" \
    --base-url "$BASE" \
    --db "$WIN_DB" \
    --backups "$WIN_BACKUPS" \
    --expect-file "$EXPECT_FILE_W" \
    --min-checks "$2"
  return $?
}

# ---- 场景组（不需要重启服务）---------------------------------------------
echo "== 场景组：E2E-04 / E2E-08 / E2E-09 / E2E-10 =="
if ! run_mode scenarios 12; then
  RC=1
fi

# ---- E2E-06 备份与恢复（真停服换库）--------------------------------------
echo
echo "== E2E-06 备份与恢复：prepare =="
if ! run_mode restore-prepare 3; then
  RC=1
fi

if [ ! -f "$EXPECT_FILE" ]; then
  echo "❌ 备份点记录缺失，跳过恢复验证" >&2
  RC=1
else
  BACKUP_FILE="$("$PY" -c "
import json,sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['backup_file'])
" "$EXPECT_FILE_W")"

  if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ 找不到备份文件：$BACKUP_FILE" >&2
    RC=1
  else
    echo "  备份文件：$BACKUP_FILE"
    echo "  停服 → 用备份替换库 → 启服（就是 ops/README.md 里的恢复步骤）"

    qa_stop_server

    # ⚠️ 必须同时清掉 -wal / -shm：留着旧 WAL，SQLite 会把备份点之后的事务
    #    重新「回放」进新库，恢复就白做了 —— 这是恢复操作最经典的坑。
    cp "$BACKUP_FILE" "$DB_FILE"
    rm -f "$DB_FILE-wal" "$DB_FILE-shm"

    qa_start_server

    echo
    echo "== E2E-06 备份与恢复：verify =="
    if ! run_mode restore-verify 5; then
      RC=1
    fi
  fi
fi

qa_finish "$RC"
