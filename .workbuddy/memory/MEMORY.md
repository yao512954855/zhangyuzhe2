# 背记系统 — 项目长期约定

> 中小学英语/语文背诵系统（局域网）。只有「管理者」「学生」，不用「老师」。1–9 年级，一个班 ~40 人。

## 文档地图
`docs/`：需求说明书(v0.6)、数据库设计(**DDL 冻结**)、接口设计、UI设计规范(§7 = APP 验收依据)。
`docs/dev/`（**注意在 docs 下面，不在仓库根**）：`00-总纲`(17包/**§10 状态表**)、`01-契约冻结`(**必读**)、`02-实现约定与复用经验`(**§八 待裁决，每包必追加**)、`10a|10b|20|30|40` 规格。
**优先级：契约冻结 > 接口设计 > 数据库设计**；契约有问题**停下来报告，不要绕过**。
`qa/`：`smoke/`(39 条) `e2e/`(12+3+5) `perf/`(**非功能 18 条 + 缺口 8 条**) `reports/`(`2026-09-25.md` 验收报告 + **`未完成与降级清单.md`**) + ASR 预研。
`backend/asr.yaml` **被 .gitignore 排除**（有 `.example` 模板）；现状 `provider: aliyun` + **`api_key: ""`** → `asr_ready=false`，**按现状真机语音跑不通**。

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
 - **一期接口缺口已「清零」**（2026-09-25 盘点 4 个，同日全部处置）：①**英语 hint 端点** → 裁决 **B** 移出本期（三档提示划归语文）；②**学生可见的班级清单** → ✅ 补**免鉴权** `GET /api/classes/options`；③**个人正确率趋势** → ❌ 裁决**一期不做**（连需求/规格/客户端组件一并摘除）；④**`GET` 场次列表/详情** → ✅ 补齐（接口设计 **§9.5**）。→ **一期范围内再无需补的接口**；清单 **A 档仅剩 A5 语文错题本**（非接口，二期）。**全仓无桩、无假数据**
- **`items[]` 只列「有作答记录的项」**（§9.5.2）：`sessions` 只存 `strategy` 快照，**题序不落库**（无 `session_items` 表）→ 不反推「当时出了哪些题」，**`items.length` 可以小于 `total_items`**

## 进度（2026-09-25 17:00）— **§八 待裁清单清空（77/77）**
**把 77 条压成「只需拍板 13 条」并全部裁决**（复核报告 `qa/reports/八区待裁清单复核与建议.md`）：
- **甲档 8 条**（有真分歧）：UI 对比度（#38/39/40）→ **改文档、不动色值**；`total_chars`（#47）→ **改 §8.6 示例 1180→620**；F15 超时（#13）/ 英语静默退化（#43）/ 申诉备注（#50）/ F7 视图（#51）→ **保持现状 + 写进契约**。
- **乙档 5 条**（追认）：**#62/#65 公共请求层 `responseType`/`headers` 正式收编进契约**（不再是"子包越权改文件"）；#57/#59 BE-08 状态存储追认；#77 `rate_limit.reset()` **改注释**。
- **丙档 53 条**：契约未定义、实现选合理说法 → 整档通过，**只备案不改码**。
- **代码改动量 = 0 行逻辑代码**（U1/U2/U4/U6 只改文档与注释；U3/U5 现状即结论；乙档仅 1 行注释）。
**UI 对比度新口径（重要）**：原文「8:1 ~ 12:1」**废止**（12:1 上限在 WCAG 中无对应条款；8:1 下限严于 AA 却无理由）→ 改为「**正文文字 ≥ 4.5:1（WCAG AA），不设上限**」。§2.1 原写「≈11.5:1」是**算错**（WCAG 实算 **13.00**）；**弱化文字三套 2.44–3.58 如实登记为「未达 AA，不视为达标」**（只承载占位/说明，一期不阻断）。同步改了 5 处口径残留：契约 §12.2 / 30-规格 F3+硬约束表 / `format.dart` / `app_theme.dart` / `theme` 单测。
**复核顺带更正清单自身 2 处**：**#40 把 `14.16:1` 挂错了对象**（那是「**正文 on 卡片**」；「**卡片 on 背景**」真值 **1.09:1**，属相邻色块层次、非文字对比度，本就不该套 AA）；#39 引用的「§2.5」实为 **§2.1 的对比度校验注**。另 **#41（ping 桩遮蔽）已闭环** —— `main.py` 的桩**已删**，`discovery.py:42` 为唯一实现。
**回写位置**：契约 **§14.1 第 9–15 项**；§八 头部加「全 77 条裁决分布」表；验收报告新增 **§6.1**；风险 #6 关闭。**下轮新不一致从 `#78` 起编号**。

## 进度（2026-09-25 16:30）— **姚工裁决 A2=摘掉 / A3=补上，清单 A 档 3→1**
**A2 摘掉**：`git rm` 掉 `widgets/trend_chart.dart`（删前确认已入库可回溯）+ 删「我的统计」趋势卡片 + 重写 `test_app05_stats_test.dart`（**加反向护栏**：断言页面上**不存在**趋势卡片）；同步摘除**需求 §5.9 / 30-规格 F3·U4 / 40-规格 #14 注 / 契约 §14.1（新增第 8 项）**的趋势口径。
**A3 补上**：契约 §14.1 **缺口 7** → 接口设计新增 **§9.5**：`GET /api/sessions`（分页 / `content_type`·`status` 过滤 / `started_at DESC` / **只回自己**）+ `GET /api/sessions/{id}`。**两条安全线**（都做了**反向验证**：撤掉后测试必须转红）——① **越权 === 不存在**，同码 `10004`；② **`ongoing` 场次拒绝下发逐题明细**（`expected` 就是答案 → 泄题），客户端**也不给入口**。语文详情走 `recitations` **纯只读**重建漏字（`content_plain[start:end]`），⚠️**绝不调 `sync_chinese_session()`**（它会 `UPDATE sessions`）。正确率**复用 `finish_session` 落库的两个计数**。
**当前计数**：后端全量 **469 passed**（458 + 11 条 §9.5 新用例）；学生端 `analyze` **0 issue**、全量 **321 passed / 56 skipped**；管理端 `typecheck` 过（本轮未改管理端）。
**真机烟测 `qa/smoke_app05_verify3.txt`：7 通过 / 0 跳过**（新增两条 §9.5 用例，真后端验 `ongoing → 10003`）。
登记 §八 **#74（裁决摘掉）/ #75（已修复）/ #77（新发现）**。

## 进度（2026-09-25 15:00）— **收口后「照文档继续开发」又修掉 4 条（A4/B2/B4/B5）**
**当日下午修掉 4 条**：**A4** 注册选班级（免鉴权 `GET /api/classes/options` + 注册页卡片列表 + **真提交 `class_id`**）｜**B2** 导入预览补 `valid_rows`（契约 §6.2 + 后端 + 管理端分页表）｜**B4** 语文逐字点亮（**原描述有误，真因 = 汉字字符集口径不一致**，已对齐）｜**B5** 结算用时改由 `/settle` 回带 `duration_ms`/`first_voice_ms`，客户端优先服务端值。→ **清单 A 4→3、B 6→3**。登记 §八 **#36 / #64 / #72 / #73**（均标「已修复」）。
**当轮计数**（15:00 快照，最新见上一段）：后端全量 **458 passed**；学生端 `analyze` **0 issue**、全量 **313 passed / 54 skipped**；管理端 `typecheck` + `build` 过。证据：`test_be02_accounts.py` / `test_be03_content.py` / `test_be05_engine.py` / `test/test_app01_register_test.dart` / `test/test_app04_recite_test.dart`。
**顺手修掉一个测试脆弱点**：`test_be02_accounts.py` 的 fixture 只重设了 `DB_PATH`（缺 `JWT_SECRET`/`ADMIN_INIT_PASSWORD`）→ 与别的模块**混跑被污染**（单跑 28/28、混跑 20 failed）；补全后 **混跑 136 passed**。

## 进度（2026-09-25 12:25）— **QA-01 验收收口完成 + 未完成清单已出**
**验收收口（本轮新增）**：非功能 **18/18**（`qa/perf_nonfunc_2026-09-25.txt`）、缺口补验 **8/8**（`qa/perf_gaps_2026-09-25.txt`）、`docs/dev/40-集成与验收规格.md` §5.1/§5.2 矩阵**已回填**（此前 50 条全 `☐`）→ **功能 37/40、非功能 8/10**；`qa/reports/2026-09-25.md` 为验收报告，`qa/reports/未完成与降级清单.md` 为「还有没做完」的正面回答（A 真缺口 4 / B 打折 6 / C 需真实环境 9 / D 二期 12 / E 流程 4）。
**前端 16 包完成**：BE-01~08、WEB-01/02/**03**、APP-01~**05**；**QA-01 也已完成**。学生端 `analyze` **0 issue**、全量 **304 passed / 54 skipped**（计数自洽 358==358）；后端全量 **453 passed**。
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
- **⚠️ 真机烟测里「每个用例各登录一次」会撞契约 §5.4 的登录限流**（**单账号 5 次/分钟**，且**成功也计数**）：只有 ≤5 条用例时全绿（刚好卡边界），**加到第 6 条突然 `10005 操作太频繁了`** —— 那不是 bug，是限流在正常工作。→ **整套用例只登录一次**（`setUpAll` 共享 `ApiClient`），并把 `MIN_LIVE_TESTS` 提到实际条数。另：`rate_limit.reset()` 的 docstring 写「登录成功后调用」，**全仓无生产代码调用**（只有测试用）→ 注释与实现不符（§八 #77）。
- **⚠️ Riverpod `Notifier.build()` 里不能同步调会读 `state` 的方法** → 抛「Tried to read the state of an uninitialized provider」。首次加载要 `Future.microtask(() => _load(...))`，等 `build()` 返回后再拉。
- **⚠️ `find.descendant` 默认 `matchRoot: false`**（根节点**不算**自己的后代）：key 打在 `InkWell` 上再找 `InkWell` 后代 → **永远 findsNothing**。断「可不可点」直接 `tester.widget(find.byKey(k))` + `isA<InkWell>()` / `isNot(isA<InkWell>())`。另：`NavigatorObserver` 会把 `home` **起始路由**也记进 `pushed` → 断言「没有跳转」前先 `spy.pushed.clear()`。

- **⚠️ 同一条消息里对「同一个文件」发多个 `Edit` 会互相覆盖**（后写的基于旧快照落盘，先写的被静默吞掉；**不同文件**并行没事）。2026-09-25 真实踩到：改「未完成清单」表头 + A1 段两处，结果表头改动消失，而我**以为已经改完**。→ 规矩：同一文件的多处改动**串行发**；改完**必须 `grep` 回扫落盘结果**（尤其矩阵/清单/报告这类交叉引用多的文档），别信「Successfully edited」。

## 待办
- [x] **git 首次入库已完成**（2026-09-25）：3 次提交 `69083f6`（基线 501 文件 / 90505 行）、`347bc0d`（入库清单）、`a55fb90`（#68 + #70）；**未 push**（无远端）。清单 `qa/git_first_commit_manifest.txt`
- [x] **§八 #68 已裁决 = A 方案并修复**（2026-09-25）：`char_grid.dart` 「当前位置」框改**纯空框**（删 `Text('字')`）+ 新增 `CharGrid.currentCellKey` 供测试定位 + `test_app04_recite_test.dart` 改为断言「框在但框内无任何字符」；依据 = 30-规格 §F2:279 / UI §7.7:522,540 / 需求 §5.6:480 四处一致。验证：`analyze` 0 issue、APP-04 `+22` 绿、全量 `+303 ~54` 绿
- [ ] **ASR 预研第三批（真人录音）不可跳过**；**判分阈值在此之前不得写死**
- [ ] 负样本扩 ≥20；重验 `en`/`zh`；补测噪声/麦克风；确认 TTS 新模型接口
- [x] **`dev/02 §八` 全 77 条已裁决完毕（待裁 0）**（2026-09-25）：已处置 11 / 甲档 8 / 乙档 5 / 丙档 53，结论回写契约 **§14.1 #9–#15**；复核报告 `qa/reports/八区待裁清单复核与建议.md`；**下轮新不一致从 `#78` 起编号，不复用已裁条目**。另：WEB-03 单元「未完成学生」口径分歧（未进清单）；#65/#66/#67/#68/#69 均已处置
- [x] **受保护文件跨包改动复核已完成**（2026-09-25）：
      · `student-app/lib/core/net/api_client.dart`（#65，APP-03 加 `headers`）→ **复核通过**：追加式、默认 null、`Authorization` 在附加头**之后**写入（覆盖不了）、自带真请求测试正反两条。
      · `admin-web/src/api/client.ts`（#62，WEB-02 加 `responseType:'blob'`）→ 方向正确但**带出 #69 静默缺陷，已修**（blob 分支先按 Content-Type 认错误体再抛 `BizError`）。**通用教训：给公共请求层加「通道能力」时，必须同时定义该通道下业务错误长什么样。**
- [ ] **BE-06 DoD 未闭合**：`AliyunProvider` 云联调（无凭证）；离线 `asr.yaml` 改 `mock`
- [x] **A1 英语三档提示已裁决 = B 方案并落地**（2026-09-25）：需求 §5.7 加「学科范围」（三档提示划归**语文**）+ 30-规格 WP-APP-03 **F12 降为单档** + 矩阵拆 `#22 语文 ☑` / `#22b 英语 ➖`（不计入分母）+ §八 #71 记裁决 + 客户端撤死入口（`escalateHint()` 封顶 `kEnglishMaxHintLevel=1`、一档后按钮置灰、删「再提示一档」文案）。依据：`/hint` 在原契约里本就标**「实现补充」**且挂在 §9.4「**语文**背诵相关接口」下 → **不是降级，是把偏离掰回契约**。验证：`analyze` 0 issue、英语测试 28 绿、全量 **304/54**、**反向验证两条护栏都转红**
- [x] **A4 / B2 / B4 / B5 已于 2026-09-25 下午修复**（照文档继续开发）→ 见「进度（15:00）」；登记 §八 #36 / #64 / #72 / #73
- [x] **A2 / A3 姚工已拍口径并落地**（2026-09-25）：**A2 = 摘掉**（一期不做个人趋势，连需求/规格/组件一并摘除）；**A3 = 补上**（接口设计 §9.5）。→ **一期待裁决项已清零**
- [ ] 建议顺手改 `backend/app/core/rate_limit.py` 的 `reset()` docstring（「登录成功后调用」与实现不符，见 §八 #77）——**未擅自改动**，等确认
- [ ] 需安卓真机；轮换明文出现过的百炼 API Key
