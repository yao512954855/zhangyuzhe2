# 背记系统 — 项目长期约定

> 中小学英语/语文背诵系统（局域网）。只有「管理者」「学生」，不用「老师」。1–9 年级，一个班 ~40 人。

## 文档地图
`docs/`：需求说明书(v0.6)、数据库设计(**DDL 冻结**)、接口设计、UI设计规范(§7 = APP 验收依据)。
`dev/`：`00-总纲`(17包/**§10 状态表**)、`01-契约冻结`(**必读**)、`02-实现约定与复用经验`(**§八 待裁决，每包必追加**)、`10a|10b|20|30|40` 规格；`qa/` ASR 预研。
**优先级：契约冻结 > 接口设计 > 数据库设计**；契约有问题**停下来报告，不要绕过**。

## 技术选型
Flutter / Vue3+Vite+TS+ElementPlus+Pinia / FastAPI+uvicorn。DB **SQLite(WAL)**、**不用 ORM**。`pypinyin==0.51.0` 锁死。端口 **8810**、UDP **9527**、管理端 `{host}:8810/admin`。
语音：语文(流式)`qwen-audio-3.1-asr-flash-streaming`；英语(非流式)`fun-asr-flash-2026-06-15`。⛔禁用 `qwen3-asr-flash`(把错读脑补成正确词)。仲裁仅 空结果/`fuzzy`/申诉，**严禁进主链路**。

## 关键业务约定
- 导入必带 **学科/年级/学期**；**有答题记录只能停用不能删**；出题默认顺序，学生自选 范围/数量/顺序/模式/限时；语文粒度**按字**
- 拼读归一为纯字母序列比对，**必须逐字母差异回显**；**切分引导只允许逐字母**；谐音表扩容序 `C/G`>R>B/P>W/V>元音
- **判分模型必须「忠实」**：选型**必须单独测负样本忠实度**（正样本高 ≠ 可用）
- 拼读语种 `zh`、整词 `en`，由**服务端**按 `task_type` 定；掌握状态口径 `not_started`/`practiced_with_error`/`practiced_ok`/`mastered`
- **语音优先（硬约束）**：键盘仅兜底、第 3 次失败才降级、留痕 `used_keyboard`
- 学生端自主注册无需审核；录音默认不保存；服务器 IP 会变 → App 必须 UDP发现+扫码+手输+最近地址；AccessKey 只在服务端

## 实现约定（详见 dev/02 §一）
- 接口一律 `ok()`/`page()`；业务失败 = 200 + 非零 code；`trace_id` 还要写进 `scope`
- 新增后端接口只放 `app/api/xxx.py` 定义 `router`，**不许改 main.py**；管理端请求只走 `api/client.ts`，页面只加 `router/routes/xxx.ts`
- 正确率唯一实现 `UserService._accuracy()`（**统计模块也必须转发它、不重写**）；登录防枚举三路径都回 `20002` 且都真跑 bcrypt
- 学生端真机联调**必须** `HttpOverrides.global=null`，否则 `flutter test` 吞真请求 → 全 skip 假绿
- **组卷请求体：语文不发 `mode`**；**课文展示只用 `recite_scope_chars`**（`total_chars` 名不符实）；**错题本一期只有英语**；英语 `scope=lesson/paragraph` 会静默退化、语文不读 `count/order` → 界面不给走不通的假选项

## 进度（2026-09-25 10:45）— **17 包全齐 + 已首次入库**
**前端 16 包完成**：BE-01~08、WEB-01/02/**03**、APP-01~**05**；**QA-01 也已完成**。学生端 `analyze` **0 issue**、全量 **303 passed / 54 skipped**（计数自洽 357==357）；后端全量 **453 passed**。
**5 包收口结果经主 Agent 独立复跑（非采信自报）**，汇总 `qa/closeout_5packages_2026-09-25.md`：
- WEB-03 17/18 ｜ APP-03 **6/6（首次真跑通）** ｜ APP-04 **15/15** ｜ APP-05 **5/5** ｜ QA-01 冒烟 39/39 + e2e 12/12。证据 `qa/smoke_app03_verify.txt`、`qa/smoke_app04_verify_indep.txt`、`qa/smoke_web03_verify.txt`、`qa/smoke_app05_verify2.txt`、`qa/e2e_qa01_run2.txt`。
- **WEB-03**：`typecheck`/`build` 过；真机烟测 **17/18**（1 项真机扫码属人工）。
- **APP-03/04/05**：代码 + 组件测试齐，`/practice`、`/recite`、`/mistakes` 三路由已接管（用 APP-01 的占位文件，**不新建 `english.dart`/`mine.dart`**）。
- **APP-04 DoD 已闭合**（2026-09-25 10:12）：补上 `scripts/smoke_app04.py`（端口 8892，临时 `ASR_CONFIG_PATH` 指 mock）+ `test/smoke_app04_live_test.dart`（**15 条真机用例，15 通过 / 0 跳过**，含**真 WS `/ws/asr` `task_type=recite` 链路**：partial 帧真的推进服务端 `pos`）。存档 `qa/smoke_app04_verified.txt`。
- **QA-01**：完成（`qa/smoke/smoke_core.py` 冒烟 **39/39**；`qa/e2e/` 场景 **12/12** + prepare 3/3 + verify 5/5，`e2e_qa01_run2.txt`）。run1 曾挂：`E2E-09` 断言 `codes_set=[-1]`，即 `int(code or -1)` 把业务成功码 `0` 当假值 → 已修。
- ✅ **APP-05 烟测 `10001` 已定位并修复**：根因是**脚本自己**把 `PUT /api/me/profile` 写成了 `post`（405 被包成 `10001`）；核实客户端与 `smoke_app02/03` 都是 `put` → 改回 `put` 后 **5/5 通过**。
- ⚠️ **APP-04 真机烟测抓到 1 个客户端真实缺陷并已修**：`/feed` 的 `newly_matched` 是**位置数组**（`[0,1,2,3,4]`），客户端却用 `_toInt()` 解析 → 真机恒为 0（`int.tryParse("[0, 1, 2, 3]")` 静默 null）；假适配器喂 `int`，所以组件测试永远绿。已加 `newlyMatchedPositions` + 兼容解析，烟测钉住作回归护栏（§八 #66）。
- ✅ **git 首次入库完成**（2026-09-25）：仓库此前**零提交** → 3 次提交 `69083f6`（基线 501 文件 / 90505 行）、`347bc0d`（入库清单）、`a55fb90`（#68 + #70）。**未 push**（无远端）。入库前做了全仓密钥扫描（`sk-*`/`AKID*`/`LTAI*` **零匹配**）+ 逐条核对 `.gitignore`（补了 `.env.*` 兜底 + `.flutter_tool_state` + `*.log`，烟测结论 `smoke_*.txt` 保留）+ 新增 `.gitattributes`（仓库内 LF、`ops/*.bat|*.ps1` 还原 CRLF）。

## 环境与踩坑
Flutter `D:\2install\flutter\flutter_windows_3.47.5-stable\flutter`；**bash 调 flutter 必崩（MSYS 缺 PATHEXT）→ 用 `scripts/flutter.sh`**；⚠️ **该脚本在仓库根**，student-app 下要写 `bash ../scripts/flutter.sh`。JDK 用 Studio 自带 JBR（机器级 `JAVA_HOME` 1.8 **不要改**）。
烟测端口：8810 正式 / 8812 APP-02 / 8891 APP-03 / 8892 APP-04 / 8893 APP-05 / 8895 BE-07 / 8896 BE-08 / 8897 BE-03 / 8898 BE-04 / 8899 BE-02,05；管理端 WEB-03 8817+5285 / 请求层回归 **8818+5286**（`admin-web/scripts/smoke_webreq_blob_error.mjs`）。
- **⚠️ 沙箱会拦「应用自己删文件」**：`PYTHONPATH` 注入的 safe-delete shim 一拒绝就 `SystemExit(1)` 杀进程（征兆：spawn 出的 uvicorn 一行日志都没有）→ 真机烟测一律 **`env -u PYTHONPATH`**。
- **⚠️ `flutter test` 不许传 `-d`**：`-d none` 会让 dart 0 CPU 空转永不结束（烧过 42 分钟）。
- **⚠️ `StreamSubscription.cancel()` 的 Future 在 `tester.pump` 下永不完成**（`test/test_app04_teardown_cancel_test.dart` 是特征化断言）→ **关键路径上 `await` 它会静默卡死**，只能 `unawaited(...)`。
- **⚠️ `flutter test` 一次 `pump()` 只推一帧**：`Navigator.push` 之后必须再 pump 几帧才能 `find` 到新页面。
- 烟测脚本「用例收集式」结论行要防假绿：**中断也要计入失败**（WEB-03 曾出现「跑一半却报 7/7 PASS」）。
- **⚠️ 请求层的「通道能力」必须连错误路径一起定义**（§八 #69）：`responseType:'blob'` 下 axios 会把「HTTP 200 + 非零 code」的 JSON 错误体**包成 Blob**，直接 `return` 就会让用户下载到内容是 `{"code":10001}` 的假 xlsx，**且统一错误提示一次都不弹**。`typecheck`/`build`/正常路径烟测**全都看不出**。
- **真浏览器验证 Web 客户端逻辑的省力做法**：vite dev 下页面内 `import('/admin/src/api/client.ts')` 就能拿到**与 App 同一份模块单例**（不是手搓 axios），配合 Edge `--remote-debugging-pipe`（CDP over pipe）即可对「生产代码路径」做真实断言；`admin-web/scripts/smoke_webreq_blob_error.mjs` 是 330 行的最小可抄骨架。
- **两个烟测不能并行**（都抢 UDP 9527）；动手前先查进程/端口。后台"端口守望"在**同一烟测连跑两次**时会误判（run1 收尾到 run2 启动间有 >30s 空档）→ 稳的判据是「无 `smoke_*.py` 进程 **且** 端口空闲」持续数轮。

## 待办
- [x] **git 首次入库已完成**（2026-09-25）：3 次提交 `69083f6`（基线 501 文件 / 90505 行）、`347bc0d`（入库清单）、`a55fb90`（#68 + #70）；**未 push**（无远端）。清单 `qa/git_first_commit_manifest.txt`
- [x] **§八 #68 已裁决 = A 方案并修复**（2026-09-25）：`char_grid.dart` 「当前位置」框改**纯空框**（删 `Text('字')`）+ 新增 `CharGrid.currentCellKey` 供测试定位 + `test_app04_recite_test.dart` 改为断言「框在但框内无任何字符」；依据 = 30-规格 §F2:279 / UI §7.7:522,540 / 需求 §5.6:480 四处一致。验证：`analyze` 0 issue、APP-04 `+22` 绿、全量 `+303 ~54` 绿
- [ ] **ASR 预研第三批（真人录音）不可跳过**；**判分阈值在此之前不得写死**
- [ ] 负样本扩 ≥20；重验 `en`/`zh`；补测噪声/麦克风；确认 TTS 新模型接口
- [ ] **`dev/02 §八` 待裁决累计 70 条**（WEB-03 单元「未完成学生」口径分歧；APP-03/04 #65 跨包改 `api_client.dart`、#66 `newly_matched` 形状、#67 `hint_count` 键集合、**#68 APP-04 `char_grid` 当前位置框偏离 F2「空框」**、**#69 blob 通道静默吞业务错误（已修）**）
- [x] **受保护文件跨包改动复核已完成**（2026-09-25）：
      · `student-app/lib/core/net/api_client.dart`（#65，APP-03 加 `headers`）→ **复核通过**：追加式、默认 null、`Authorization` 在附加头**之后**写入（覆盖不了）、自带真请求测试正反两条。
      · `admin-web/src/api/client.ts`（#62，WEB-02 加 `responseType:'blob'`）→ 方向正确但**带出 #69 静默缺陷，已修**（blob 分支先按 Content-Type 认错误体再抛 `BizError`）。**通用教训：给公共请求层加「通道能力」时，必须同时定义该通道下业务错误长什么样。**
- [ ] **BE-06 DoD 未闭合**：`AliyunProvider` 云联调（无凭证）；离线 `asr.yaml` 改 `mock`
- [ ] 需安卓真机；轮换明文出现过的百炼 API Key
