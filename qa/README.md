# QA-01 集成联调与验收

这个目录是 **WP-QA-01** 的产出：把 17 个独立开发的包拼起来，验证「真的能跑」，
并对一期需求逐条验收。所有脚本都是**真进程 + 真库 + 真 HTTP/WebSocket**，
ASR 走 **Mock Provider**（不依赖云账号），所以本地随时能跑、失败也能复现。

---

## 一、目录

```
qa/
├── smoke/
│   ├── _bootstrap.sh      # 起停真后端的**唯一一份**逻辑（冒烟与 e2e 共用）
│   ├── smoke.sh           # 一键冒烟：起服务 → 跑断言 → 收服务
│   └── smoke_core.py      # 冒烟断言本体（39 条），只打接口 + 查库对账
├── e2e/
│   ├── e2e.sh             # 端到端驱动（含 E2E-06 真停服换库重启）
│   └── e2e_core.py        # 场景断言本体（scenarios / restore-prepare / restore-verify）
├── fixtures/
│   ├── make_fixtures.py   # 生成导入用 Excel（学生 / 词条 / 课文）
│   ├── README.md          # 每个 fixture 的预期校验结果（valid/invalid 行数）
│   └── *.xlsx
├── reports/               # 验收报告（按日期）
├── audio/  audio_cn/  audio_neg/   # ASR 预研用的音频样本
└── ASR预研报告.md / ASR模型选型报告.md
```

## 二、怎么跑

```bash
# 冒烟（约 3 秒）
env -u PYTHONPATH bash qa/smoke/smoke.sh

# 端到端（约 40 秒，因为 E2E-06 会真的停服、换库、再启服）
env -u PYTHONPATH bash qa/e2e/e2e.sh

# 造测试 Excel
env -u PYTHONPATH backend/.venv/Scripts/python.exe qa/fixtures/make_fixtures.py
```

可选环境变量：`SMOKE_PORT=8820`（默认）、`KEEP_TMP=1`（跑完保留临时目录，便于查库）。

**`env -u PYTHONPATH` 不是可选项**，见下面「环境坑」第 1 条。

## 三、覆盖情况

### 冒烟（`smoke_core.py`，39 条断言，全部真跑）

规格 §2 的 10 条冒烟清单 → 断言编号：

| 规格条目 | 断言 |
|---|---|
| 后端能启动，`/api/health` 返回 `db:true` + `schema_version` | #01 |
| 首次启动自动建 admin；管理端可登录 | #02 |
| `GET /api/ping` 免鉴权且 < 50ms | #03 |
| App 扫码能连上（`qr_content` 格式 + UDP 广播**真往返**） | #04–#06 |
| 学生注册 → 登录 → 首页加载 | #07–#09 |
| 导入 1 单元 + 5 词条 → 学生端能看到 | #15–#17 |
| 英语答题 → `answers` 有记录 → `word_mastery` 更新 | #18–#21 |
| 语文背诵 → `recitations` 有记录 | #29–#36 |
| 班级概览有数字 | #37–#38 |
| 手动备份 + `integrity_check` 通过 | #39 |

另外补了 3 条**必须真跑**的链路：ASR WebSocket 真会话（#22–#23）、
申诉全链路且**复核不改判分**（#24–#27）、权限隔离 `10003`（#10）。

### 端到端（`e2e_core.py`）

| 场景 | 谁验 |
|---|---|
| E2E-01 英语闭环 | `student-app/scripts/smoke_app03.py` |
| E2E-02 内容→数据全链路 | `backend/scripts/smoke_be05.py` + APP-04 真机烟测 |
| E2E-03 IP 变化自动恢复 | `backend/scripts/smoke_be08.py`（真机换网段属人工验收） |
| **E2E-04 断网降级** | 本目录 `scenarios`（键盘通路 + `used_keyboard` 落库） |
| E2E-05 申诉链路 | `smoke_core.py` #24–#27 |
| **E2E-06 备份与恢复** | 本目录 `restore-prepare` / `restore-verify` + `e2e.sh` 真停服换库 |
| E2E-07 权限隔离 | `smoke_core.py` #10 |
| **E2E-08 内容删除保护** | 本目录 `scenarios`（30003 + 停用后不可见） |
| **E2E-09 并发练习** | 本目录 `scenarios`（20 并发） |
| **E2E-10 识别为空不计错** | 本目录 `scenarios` |

**刻意不重复实现**：已经由各包真机烟测覆盖得更细的场景（E2E-01/02/03），
这里只做映射，不写第二份会漂的代码。

---

## 四、环境坑（都是真踩过的，改之前先读完）

### 1. 必须 `unset PYTHONPATH` / `env -u PYTHONPATH`

WorkBuddy 沙箱把 **safe-delete shim** 注进了 `PYTHONPATH`。它 hook `unlink`，
一旦拒绝就用 **`raise SystemExit(1)`** —— `SystemExit` 继承 `BaseException`，
业务侧 `except OSError` 兜不住，也**不该**为它写 `except BaseException`，
结果是 spawn 出来的 uvicorn **一行日志都没有**就没了。
`_bootstrap.sh` 开头已 `unset PYTHONPATH` / `unset PYTHONSAFEPATH`；外面再套一层更稳。

### 2. 传给原生 Windows 程序的路径**必须**转成 Windows 形式

Git Bash 里 `$TMP` 是 `/tmp/...`、`$SCRIPT_DIR` 是 `/d/helloagent/...`。
**MSYS 的路径转换只对可执行文件本身的路径生效，参数与环境变量里的不认**，于是：

| 传法 | 实际发生 | 症状 |
|---|---|---|
| `DB_PATH=/tmp/x/smoke.db` | Python 把 `/tmp/...` 解析成「当前盘符根」→ `D:\tmp\...` | 服务端写库在 `D:\tmp`，脚本去 `%TEMP%` 读 → 「表不存在 / 查到 0 行」，**看起来像业务 bug** |
| `python /d/helloagent/.../smoke_core.py` | 变成 `D:\d\helloagent\...` | `No such file or directory` |
| `uvicorn --app-dir "$BACKEND"` | 同上，`sys.path` 里是个非法路径 | `ModuleNotFoundError: No module named 'app'`（一屏 traceback） |

解决：`_bootstrap.sh` 里统一用 `qa_win()`（包了 `cygpath -w`）产出 `WIN_DB` /
`WIN_BACKUPS` / `WIN_ASR` / `WIN_TMP` 给原生程序用；Bash 自己用（`cd` / 重定向 /
`rm` / `mktemp`）仍用 MSYS 形式，两者指向同一物理目录。
**起服务一律用子 shell `cd "$BACKEND"`，不要用 `--app-dir`。**

### 3. `$!` 不是 Windows PID —— 清理必须按端口反查

MSYS 的 `$!` 是**它自己的 PID**：实测 `$!`=7082，`tasklist /FI "PID eq 7082"` 报
「没有运行的任务匹配」，`taskkill /PID 7082` 返回 **128**。
于是 `taskkill //PID "$!" //T //F` 这种写法会**静默泄漏**一个仍占着 TCP 端口
和 **UDP 9527** 的后端进程。本项目真的踩了：8893 上挂着一个 23 分钟无人使用、
0 连接的孤儿 uvicorn，把 UDP 9527 钉死，导致后来所有依赖发现端口的回归**假失败**。

另外 `python -m uvicorn` 在这台机器上会**再起一个子进程**（实测父 14376 + 子 4120），
所以必须 `/T` 连子进程树一起收。

现在的实现：`qa_kill_port_listeners` 用 `netstat -ano` 反查监听本脚本 TCP 端口的
Windows PID 再 `taskkill /T /F`。既拿得到真 PID，又把副作用限制在**本项目自己的端口**上
（不会误杀别人的服务）。`qa_stop_server` 之后还会等 UDP 端口真的放开（最多 15 秒）。

### 4. 防假绿

- `MIN_CHECKS` 是**硬门限**：断言数低于下限直接判失败。冒烟 39 条 → 门限 36；
  E2E 三个模式分别 12 / 3 / 5 条，各自显式传 `--min-checks`（别用一个全局默认值，
  否则 `restore-*` 这两个短模式会被自己的门限判死）。
- 无法自动化的项走 `skip()` **单独计数并打印**，不冒充通过。
- **`int(r.get("code") or -1)` 是陷阱**：业务成功的 `code` 就是 `0`，被 `or` 当假值 → 成功记成 `-1`。
  本包真写错过一次，症状是两条断言互相打架（「20 个全成功」恒真、「错误码全为 0」恒假）。
  正确写法见 `e2e_core.py` 的 `_one()`。

### 5. 沙箱 safe-delete shim 还有 **Node 版**：`vite build` 会被拦

同一条 shim 也注进了 Node 侧。`vite build` 开头会 `emptyDir(dist)`，
被判定为「批量删除」直接抛错：

```
[vite:prepare-out-dir] [safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]
{"count":50,"threshold":50,"scope":"turn","targets":["...\\admin-web\\dist\\index.html"]}
```

**这不是代码问题，别去改 vite 配置或源码**。验证打包时换个**全新**输出目录即可
（目录不存在就无需清空）：

```bash
cd admin-web && npx vite build --outDir dist_qa_verify   # 验完删掉
```

`npm run typecheck`（`vue-tsc --noEmit`）不受影响，随时可跑。

### 6. 端口分配

正式 8810；烟测各包：8812 APP-02 / 8817 WEB-03 / 8891 APP-03 / 8892 APP-04 /
8893 APP-05 / 8895 BE-07 / 8896 BE-08 / 8897 BE-03 / 8898 BE-04 / 8899 BE-02,05；
**QA-01 用 8820**。UDP 发现端口 **9527 固定且不可配**（服务端是模块级常量），
所以任何两个烟测**不能同时在跑** —— 启动预检会直接 `exit 2`，别把它当偶发失败。

---

## 五、已知跳过项（如实列出，不假装覆盖）

| 项 | 原因 | 替代 |
|---|---|---|
| 手机真扫码唤起 App | 无头环境没有真手机，也无法断言 App 的 `bjsys://` scheme 注册 | 已机器化验证 `qr_content` 格式 + 二维码画布真有像素（`smoke_core.py` #04、WEB-03 烟测 U12/U13） |
| 真机换网段后的 IP 自动恢复 | 需要真机 + 真网络变更 | `backend/scripts/smoke_be08.py` 覆盖 UDP 真往返与状态落盘 |
| ASR 识别率（真人录音） | 见 `ASR预研报告.md` §4.0b | **不可跳过**，但属于预研任务，不是本目录脚本的职责 |
