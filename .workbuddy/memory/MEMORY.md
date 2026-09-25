# 背记系统 — 项目长期约定

> 中小学英语/语文背诵系统（局域网）。只有「管理者」「学生」，不用「老师」。1–9 年级，一班 ~40 人。

## 文档地图与优先级
`docs/`：需求说明书(v0.6) · 数据库设计(**DDL 冻结**) · 接口设计 · UI 设计规范(§7 = APP 验收依据)。
`docs/dev/`（**在 docs 下面，不在仓库根**）：`00-总纲`(17 包 / **§10 状态表**) · `01-契约冻结`(**必读**) · `02-实现约定与复用经验`(**§八 待裁清单，每包必追加**) · `10a|10b|20|30|40` 规格 · `50-二期工作包拆解`。
**优先级：契约冻结 > 接口设计 > 数据库设计**；契约有问题**停下来报告，不要绕过**。
`qa/`：`smoke/` · `e2e/` · `perf/`(非功能 18 + 缺口 8) · `reports/`(`2026-09-25.md` 验收报告 · `未完成与降级清单.md` · **`二期上线前人工移交清单.md`**) · ASR 预研。
`backend/asr.yaml` **被 .gitignore 排除**(有 `.example`)；现状 `provider: aliyun` + **`api_key: ""`** → `asr_ready=false`，**按现状真机语音跑不通**（部署前必须填百炼 key 或改 `mock`）。

## 技术选型
Flutter / Vue3+Vite+TS+ElementPlus+Pinia / FastAPI+uvicorn。DB **SQLite(WAL)**、**不用 ORM**。`pypinyin==0.51.0` 锁死。端口 **8810**、UDP **9527**、管理端 `{host}:8810/admin`。
语音：语文(流式)`qwen-audio-3.1-asr-flash-streaming`；英语(非流式)`fun-asr-flash-2026-06-15`。⛔禁用 `qwen3-asr-flash`(把错读脑补成正确词)。仲裁仅 空结果/`fuzzy`/申诉，**严禁进主链路**。

## 关键业务约定
- 导入必带 **学科/年级/学期**；**有答题记录只能停用不能删**；出题默认顺序，学生自选 范围/数量/顺序/模式/限时；语文粒度**按字**
- 拼读归一为纯字母序列比对，**必须逐字母差异回显**；**切分引导只允许逐字母**；谐音表扩容序 `C/G`>R>B/P>W/V>元音
- **判分模型必须「忠实」**：选型**必须单独测负样本忠实度**（正样本高 ≠ 可用）
- 拼读语种 `zh`、整词 `en`，由**服务端**按 `task_type` 定；掌握状态 `not_started`/`practiced_with_error`/`practiced_ok`/`mastered`
- **语音优先（硬约束）**：键盘仅兜底、第 3 次失败才降级、留痕 `used_keyboard`
- 学生端自主注册无需审核；录音默认不保存；服务器 IP 会变 → App 必须 UDP发现+扫码+手输+最近地址；AccessKey 只在服务端

## 实现约定（详见 dev/02 §一）
- 接口一律 `ok()`/`page()`；业务失败 = 200 + 非零 code；`trace_id` 也要写进 `scope`
- 新增后端接口只放 `app/api/xxx.py` 定义 `router`，**不许改 main.py**（自动发现）；管理端请求只走 `api/client.ts`，页面只加 `router/routes/xxx.ts`（`import.meta.glob` 自动聚合）
- 正确率唯一实现 `UserService._accuracy()`（**统计模块也必须转发它、不重写**）；登录防枚举三路径都回 `20002` 且都真跑 bcrypt
- 学生端真机联调**必须** `HttpOverrides.global=null`，否则 `flutter test` 吞真请求 → 全 skip 假绿
- **组卷请求体：语文不发 `mode`**；**课文展示只用 `recite_scope_chars`**（`total_chars` 名不符实）；英语 `scope=lesson/paragraph` 静默退化、语文不读 `count/order` → 界面不给走不通的假选项
- **`items[]` 只列「有作答记录的项」**（§9.5.2）：题序**不落库**（无 `session_items` 表）→ **`items.length` 可小于 `total_items`**
- **DDL「冻结」的准确含义 = 「已发布的迁移不可改」，不是「永不新增」** → 新增表走**新迁移文件**（`002_wave2.sql` 已加 7 张表），⛔ `001_init.sql` 永不改（§八 #85）

## 当前状态（2026-09-25 收口）
**一期**：功能验收 **38/40**（2 条 C 档真实环境）、非功能 8/10；**清单 A 0 / B 0 / E 7 全收口**；**§八 #1–#90 全裁（待裁 0，下轮从 #91 起）**。
**二期（13 条全做，姚工 2026-09-25 裁决）**：**11 条已落地** —— WP-2A 趋势 / 2B Word→Excel 脚本 / 2C 多模态兜底 / 2D 实时大屏 / 2E 回放 / 2F SRS / 2G 作业 / 2H 练习方案 / 2I 内容版本 / 2J 英语 hint / 2K 游戏化。
- ⏳ **WP-2L 家长端：一行未写**（形态未定，契约 §13 二期第 17 条禁「未定就动码」）
- ⚠️ **WP-2M 离线缓存：只做「范围 A」**（缓存+提交队列+重连幂等同步，`lib/core/offline/`）；范围 B（全量可离线练习）未做
- **独立复跑**：后端 `pytest` **608 passed / 0 failed**；学生端 `analyze` **0 error** + `test` **387 passed / 59 skipped**；管理端 `typecheck` + `vite build` 通过
- **上线前 9 条人工项**（真机/真云/真实日历/真人）见 `qa/reports/二期上线前人工移交清单.md`；⚠️ 其中 **C1/C2 同一把百炼 key，会一票卡住真机验收**
- 提交：`a2a3924`(后端) `1a00788`(学生端) `0c5330a`(管理端) `0e84534`(文档+脚本)，**未 push**（无远端）

## 环境与踩坑（高价值）
Flutter `D:\2install\flutter\flutter_windows_3.47.5-stable\flutter`；**bash 调 flutter 必崩（MSYS 缺 PATHEXT）→ 用 `scripts/flutter.sh`**；⚠️ **该脚本在仓库根**，student-app 下写 `bash ../scripts/flutter.sh`。JDK 用 Studio 自带 JBR（机器级 `JAVA_HOME` 1.8 **不要改**）。
烟测端口：8810 正式 / 8812 APP-02 / 8891 APP-03 / 8892 APP-04 / 8893 APP-05 / 8895 BE-07 / 8896 BE-08 / 8897 BE-03 / 8898 BE-04 / 8899 BE-02,05；管理端 WEB-03 8817+5285 / 请求层回归 8818+5286。
- **⚠️ 沙箱拦「应用自己删文件」**：safe-delete shim 一拒绝就 `SystemExit(1)` 杀进程（征兆：uvicorn 一行日志都没有）→ 真机烟测一律 **`env -u PYTHONPATH`**
- **⚠️ 沙箱 safe-delete 有「批量阈值」**：`npm run build` 会因 vite 清 `dist/assets`(61 文件 > 50 阈值) 被拦 → **构建输出到全新目录**（`vite build --outDir "$TEMP/xxx"`），别原地清空（§八 #90）
- **⚠️ `flutter test` 不许传 `-d`**：`-d none` 让 dart 0 CPU 空转永不结束（烧过 42 分钟）
- **⚠️ 同一条消息里对「同一个文件」发多个 `Edit` 会互相覆盖**（后写的基于旧快照落盘）→ 同一文件的多处改动**串行发**，改完**必须 grep 回扫落盘结果**
- **⚠️ pytest `-k` 过滤会破坏「用例间依赖」**：本项目大量用例靠**前序用例造的数据**（如 be07 的 U9→U10）→ 单跑一个用例会假红；**验证要么跑整个文件，要么跑全量**
- **⚠️ riverpod 3.x 默认自动重试**：`FutureProvider` 出错后会按 `defaultRetry`（首个 200ms）`invalidateSelf` 自动重发 → 错误态被覆盖、测试抖动。有显式「再试一次」按钮的页面应显式 `retry: (_, _) => null`
- **⚠️ Riverpod `Notifier.build()` 里不能同步调会读 `state` 的方法** → 抛 uninitialized provider；用 `Future.microtask`
- **⚠️ 烟测「收集式」结论行中断也要记账**，否则**中断也打印 N/N PASS**（只有退出码是 1，极易被管道吞）；**断言依赖「类名/元素存在」时，被别的包重构即静默腐化** → 跨包烟测要跟着被改方一起回归（§八 #83/#84）
- **⚠️ 真机烟测「每用例各登录一次」会撞契约 §5.4 登录限流**（5 次/分钟、**成功也计数**）：第 6 条突然 `10005` → **整套只登录一次**（`setUpAll` 共享 client）
- **⚠️ 公共请求层加「通道能力」必须连错误路径一起定义**（§八 #69）：`responseType:'blob'` 下 axios 把「200+非零 code」的 JSON 错误体**包成 Blob** → 用户下载到假 xlsx 且**错误提示一次都不弹**；`typecheck`/`build`/正常路径烟测**全看不出**
- **真浏览器验证 Web 客户端逻辑**：vite dev 下页面内 `import('/admin/src/api/client.ts')` 拿到**与 App 同一份模块单例**，配 Edge `--remote-debugging-pipe`(CDP over pipe) 即可对生产代码路径做真实断言；骨架 `admin-web/scripts/smoke_webreq_blob_error.mjs`
- **⚠️ `find.descendant` 默认 `matchRoot: false`** → key 打在 `InkWell` 上再找 `InkWell` 后代**永远 findsNothing**；断「可不可点」直接 `tester.widget(find.byKey(k))` + `isA<InkWell>()`。另 `NavigatorObserver` 会把 `home` 起始路由记进 `pushed` → 断「没有跳转」前先 `clear()`
- **⚠️ `StreamSubscription.cancel()` 的 Future 在 `tester.pump` 下永不完成** → 关键路径只能 `unawaited(...)`；`flutter test` 一次 `pump()` 只推一帧，`Navigator.push` 后要多 pump 几帧
- **两个烟测不能并行**（都抢 UDP 9527）；稳的判据是「无 `smoke_*.py` 进程 **且** 端口空闲」持续数轮

## 并行子 agent 开发的两条硬教训（§八 #87 / #88）
1. **「自报完成」≠ 交付**：二期批次 2 三个包自报完成，实际 ① 一个**编译 error**（漏 import）② 一个**渲染即崩 + 6 用例全红** ③ 三个页面**全都无入口**。→ **每包交付前必须跑「全项目级」`analyze` + `test`**（不是只跑自己那份），并确认新页面**至少一个可达入口**
2. **「独占文件」约束会留下接线缺口**：子 agent 正确地「停下报告」，但**不补就等于没做**（兜底引擎成死代码 / 回放页进不去 / 依赖没登记）。→ 拆包时要么给**接线处**指定 owner，要么在集成清单里显式列「跨包接力项」

## 待办
- [ ] **ASR 预研第三批（真人录音）不可跳过**；**判分阈值在此之前不得写死**
- [ ] 负样本扩 ≥20；重验 `en`/`zh`；补测噪声/麦克风；确认 TTS 新模型接口
- [ ] **BE-06 DoD 未闭合**：`AliyunProvider` 云联调（无凭证）；轮换明文出现过的百炼 API Key
- [ ] 需安卓真机
- [ ] **待姚工拍板**：① 家长端形态（学生端只读视图 / 独立 H5 / 独立 App）② 离线缓存是否做范围 B
