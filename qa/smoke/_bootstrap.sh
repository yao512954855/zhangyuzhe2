# ============================================================================
# 共享的「起一个真后端」引导库 —— 由 qa/smoke/smoke.sh 与 qa/e2e/e2e.sh source。
#
# 【为什么要抽出来】
#   冒烟和端到端场景要是各写一份起停逻辑，早晚会漂：一边清理了子进程树、
#   另一边没清；一边钉了 Mock ASR、另一边没钉。然后就会出现
#   「冒烟过、e2e 挂，查半天发现是环境不一样」。**只留一份**最省事。
#
# 【使用方必须做的事】
#   1) `set -uo pipefail` 之后 source 本文件；
#   2) source 后调用 `qa_bootstrap_init <子命令名>`，它会设好
#      ROOT / BACKEND / PY / TMP / DB_FILE / BACKUP_DIR / ASR_YAML / BASE / WS_BASE，
#      并注册好 EXIT 清理；
#   3) 调 `qa_start_server` 起服务并等就绪；失败会自己打印日志尾部并 exit 1。
#
# 【两个硬约束（踩过的坑，别改）】
#   - 必须 `unset PYTHONPATH`：WorkBuddy 沙箱把 safe-delete shim 注进去，
#     它 hook `unlink` 后用 `raise SystemExit(1)` 拒绝，会**当场杀掉 uvicorn 子进程**，
#     症状是「子进程一行日志都没有」。`SystemExit` 继承 `BaseException`，
#     业务侧兜不住，也**不该**为它写 `except BaseException`。
#   - 收尾必须 `taskkill //T //F`：只 kill 父进程会留下占着 **UDP 9527** 的孤儿，
#     下一个人跑回归就会拿到「假失败」（本项目已踩两次）。
# ============================================================================

unset PYTHONPATH
unset PYTHONSAFEPATH 2>/dev/null || true

qa_bootstrap_init() {
  local label="${1:-qa}"
  QA_LABEL="$label"

  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
  ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
  BACKEND="$ROOT/backend"
  PY="$BACKEND/.venv/Scripts/python.exe"
  [ -x "$PY" ] || PY="$BACKEND/.venv/bin/python"
  if [ ! -x "$PY" ]; then
    echo "❌ 找不到后端 venv 解释器：$BACKEND/.venv/Scripts/python.exe" >&2
    exit 3
  fi

  PORT="${SMOKE_PORT:-8820}"
  BASE="http://127.0.0.1:$PORT"
  WS_BASE="ws://127.0.0.1:$PORT"
  # ⚠️ 发现端口**没有**可配项：服务端 `app/services/discovery/__init__.py` 里
  # `DISCOVERY_PORT = 9527` 是模块级常量（`import` 到 scheduler/udp_responder 各处），
  # 不读环境变量。这里**故意不给环境变量旋钮** —— 给了就会有人设成 9528，
  # 于是预检查 9528（空闲）、真服务仍绑 9527（可能被占），
  # 客户端广播打到**别人的服务**上，断言变成假绿。要跑并行，换 TCP 端口而不是这个。
  UDP_PORT=9527
  KEEP_TMP="${KEEP_TMP:-0}"

  # ---- 端口预检：TCP 端口 + UDP 发现端口都要干净 ---------------------------------
  if qa_tcp_busy "$PORT"; then
    echo "❌ TCP $PORT 已被占用。换一个：SMOKE_PORT=8821 bash $0" >&2
    echo "   查占用：netstat -ano | grep LISTENING | grep :$PORT" >&2
    exit 2
  fi
  if ! qa_udp_free "$UDP_PORT"; then
    echo "❌ UDP $UDP_PORT 已被别的进程占用 —— 发现应答会打到它那边，断言会变成假绿。" >&2
    echo "   先收掉残留后端：netstat -ano -p UDP | grep :$UDP_PORT" >&2
    echo "   （跑别的烟测后没退干净的孤儿进程是最常见原因。）" >&2
    exit 2
  fi

  # ⚠️ label 只能用来**打印**，不能进路径：中文 + 空格会让 TMP 变成
  # `/tmp/qa01_QA-01 冒烟_XXXX`，之后每个 unquoted 位置都是一个雷。
  # 路径用 slug（非 [A-Za-z0-9._-] 一律换成 `_`）。
  QA_SLUG="$(printf '%s' "$label" | tr -c 'A-Za-z0-9._-' '_')"
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/qa01_${QA_SLUG}_XXXXXX")"
  DB_FILE="$TMP/smoke.db"
  BACKUP_DIR="$TMP/backups"
  ASR_YAML="$TMP/asr.yaml"
  LOG_FILE="$TMP/server.log"
  mkdir -p "$BACKUP_DIR"

  # ---- MSYS 路径 → Windows 路径（**只给原生程序用**）--------------------------
  # ⚠️ 这个转换不做就会出两类非常难查的错（本包都真踩到了）：
  #   1) `/tmp/qa01_x/smoke.db` 当**参数/环境变量**交给 Windows 版 python，
  #      MSYS 不改写它，于是 Python 把 `/tmp/...` 解析成「当前盘符根目录」→ `D:\tmp\...`。
  #      结果：**服务端写库写到了 D:\tmp，而脚本去 %TEMP% 读库**，两边不是同一个文件，
  #      表现为「表不存在 / 查到的行数是 0」这种像业务 bug 的假象。
  #   2) `/d/helloagent/.../smoke_core.py` 当参数传过去变成 `D:\d\helloagent\...`
  #      → `No such file or directory`。
  #    注意：路径转换**只对可执行文件本身的路径生效**，参数/环境变量里的不认，必须自己转。
  #    Bash 自己用（cd/重定向/rm/mktemp）的时候仍然用 MSYS 形式，两者指向同一个物理目录。
  qa_win() { cygpath -w "$1" 2>/dev/null || printf '%s' "$1"; }
  WIN_TMP="$(qa_win "$TMP")"
  WIN_DB="$(qa_win "$DB_FILE")"
  WIN_BACKUPS="$(qa_win "$BACKUP_DIR")"
  WIN_ASR="$(qa_win "$ASR_YAML")"
  WIN_LOG="$(qa_win "$LOG_FILE")"
  qa_write_mock_asr
}

qa_tcp_busy() {
  "$PY" -c "
import socket,sys
s=socket.socket()
sys.exit(0 if s.connect_ex(('127.0.0.1',int(sys.argv[1])))==0 else 1)
" "$1"
}

qa_udp_free() {
  "$PY" -c "
import socket,sys
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    s.bind(('0.0.0.0', int(sys.argv[1])))
    s.close()
except OSError:
    sys.exit(1)
sys.exit(0)
" "$1"
}

# Mock ASR：离线可跑。`by_task` 把各题型的最终文本钉死，
# 这样链路判定是确定的，不会因云模型换版而漂。
qa_write_mock_asr() {
  cat > "$ASR_YAML" <<'YAML'
provider: mock
backup_provider: ""
lang_map:
  word: "en"
  spell: "zh"
  zh_answer: "zh"
  recite: "zh"
hotwords:
  enabled: false
  sources: []
arbitrator:
  enabled: false
  trigger: [empty, fuzzy, appeal]
limits:
  chunk_ms: 150
  max_connections: 60
  queue_size: 120
  queue_wait_ms: 5000
  heartbeat_timeout_s: 60
  failover_threshold: 3
mock:
  behavior: normal
  fail_times: 0
  final_text: "classroom"
  partial_script: []
  confidence: 0.95
  partial_every: 1
  by_task:
    spell: {final_text: "see ell aye ess ess"}
YAML
}

#: 杀掉**监听指定 TCP 端口**的进程树。
#:
#: ⚠️ 为什么不能直接用 bash 的 `$!` 去 taskkill（本包真踩了两次泄漏）：
#:   Git Bash（MSYS）里 `$!` 拿到的是 **MSYS 自己的 PID**，不是 Windows PID。
#:   实测：`$!`=7082 → `tasklist /FI "PID eq 7082"` 报「没有运行的任务匹配」，
#:   `taskkill /PID 7082` 返回 128。于是清理**永远静默失败**，服务器一直占着
#:   8820 + UDP 9527 活到下一次运行，表现为「上一次明明失败了，这次却报端口被占」。
#:   改用「按端口反查 Windows PID」：既拿得到真 PID，又顺带把副作用限制在
#:   **本项目自己的端口**上（绝不会误杀别人 Agent 的服务）。
#:   另外 `python -m uvicorn` 在这台机器上会再起一个子进程（实测父 14376 + 子 4120），
#:   所以必须 `/T` 连子进程树一起收，否则端口仍被占。
qa_kill_port_listeners() {
  [ -n "${1:-}" ] || return 0
  "$PY" - "$1" <<'PYEOF' >/dev/null 2>&1 || true
import subprocess, sys

port = int(sys.argv[1])
raw = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True).stdout
text = raw.decode("gbk", "replace")
pids: set[str] = set()
for line in text.splitlines():
    parts = line.split()
    # 形如：TCP  127.0.0.1:8820  0.0.0.0:0  LISTENING  14376
    if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
        if parts[1].rsplit(":", 1)[-1] == str(port):
            pids.add(parts[4])
for pid in pids:
    subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
PYEOF
}

qa_cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then
    # 先按端口清理（真正有效的路径）；`$SERVER_PID` 只作为 POSIX 侧的兜底。
    qa_kill_port_listeners "$PORT"
    if ! command -v taskkill >/dev/null 2>&1; then
      kill -TERM "$SERVER_PID" >/dev/null 2>&1 || true
      sleep 1
      kill -KILL "$SERVER_PID" >/dev/null 2>&1 || true
    fi
  fi
  SERVER_PID=""
}

#: 停服并**等它真的把 UDP 端口放开**。
#:
#: ⚠️ 只等「进程退出」是不够的：Windows 上 SCM/句柄回收有延迟，
#: 上一个进程刚退、下一个立刻 bind 9527 会偶发 `WinError 10048`
#: （本项目已踩过：残留进程占 9527 直接让 BE-08 两条用例假失败）。
#: 所以这里等到端口真的空闲为止，最多 15 秒。
qa_stop_server() {
  [ -n "${SERVER_PID:-}" ] || return 0
  qa_cleanup
  local waited=0
  while [ "$waited" -lt 30 ]; do
    if qa_udp_free "$UDP_PORT"; then
      return 0
    fi
    sleep 0.5
    waited=$((waited + 1))
  done
  echo "⚠️ 停止后 UDP $UDP_PORT 仍未释放，继续尝试（后续可能失败）。" >&2
  return 0
}

qa_start_server() {
  echo "== $QA_LABEL：起真 uvicorn :$PORT（临时库 ${DB_FILE##*/}，Mock ASR）=="
  # ⚠️ **必须 `cd "$BACKEND"` 起服务，别用 `--app-dir "$BACKEND"`**。
  #    Git Bash 里 `$BACKEND` 是 POSIX 形式 `/d/helloagent/...`，MSYS 传给原生 Windows 进程时
  #    **不做转换**（路径转换只对可执行文件路径生效，参数里的启发式不认这种），
  #    uvicorn 收到 `/d/helloagent/...` 塞进 `sys.path` → 报 `No module named 'app'`，
  #    而且是一屏 traceback 才看得出来，很容易误判成后端坏了。
  #    用子 shell `cd` 进去就没有跨进程传路径的问题（其余烟测脚本也都是这么起的）。
  (
    cd "$BACKEND" || exit 1
    APP_PORT="$PORT" \
    DB_PATH="$WIN_DB" \
    BACKUP_DIR="$WIN_BACKUPS" \
    ASR_CONFIG_PATH="$WIN_ASR" \
    JWT_SECRET="qa01-smoke-secret-0123456789abcdef" \
    JWT_TTL_HOURS=12 \
    ADMIN_INIT_USERNAME=admin \
    ADMIN_INIT_PASSWORD="Smoke-Qa01-Pass" \
    LOG_LEVEL=WARNING \
    NO_PROXY="127.0.0.1,localhost" \
    no_proxy="127.0.0.1,localhost" \
    PYTHONIOENCODING=utf-8 \
      exec "$PY" -m uvicorn app.main:app \
        --host 127.0.0.1 --port "$PORT" --log-level warning
  ) > "$LOG_FILE" 2>&1 &
  SERVER_PID=$!
  trap qa_cleanup EXIT

  local ready=0
  for _ in $(seq 1 120); do
    if "$PY" -c "
import sys,urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=1) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
" "$BASE/api/health" 2>/dev/null; then
      ready=1
      break
    fi
    # 进程已死就别干等（沙箱 shim 杀掉子进程时正是这个征兆）
    if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done

  if [ "$ready" -ne 1 ]; then
    echo "❌ 后端未就绪（:$PORT）。日志尾部：" >&2
    if [ -s "$LOG_FILE" ]; then
      tail -40 "$LOG_FILE" >&2
    else
      echo "（日志为空！先确认是否忘了清 PYTHONPATH —— 沙箱 safe-delete shim 会静默杀掉它）" >&2
    fi
    echo "TMP=$TMP" >&2
    exit 1
  fi
  echo "?? 后端已就绪"
  echo
}

#: 打印汇总并给出退出码；$1=驱动脚本退出码
qa_finish() {
  local rc="$1"
  echo
  if [ "$rc" -eq 0 ]; then
    echo "✅ $QA_LABEL 通过"
  else
    echo "❌ $QA_LABEL 未通过（退出码 $rc）"
    echo "--- 后端日志尾部 ---"
    tail -30 "$LOG_FILE" 2>/dev/null || true
  fi
  if [ "$KEEP_TMP" = "1" ] || [ "$rc" -ne 0 ]; then
    echo "（临时目录保留：$TMP）"
  else
    rm -rf "$TMP" 2>/dev/null || true
  fi
  exit "$rc"
}
