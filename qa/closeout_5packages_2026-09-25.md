# 剩余 5 包收口 · 独立验证报告

> 日期：2026-09-25 ｜ 验证方：主 Agent（**独立复跑，非采信子 Agent 自报**）
> 对象：WEB-03、APP-03、APP-04、APP-05、QA-01
> 结论：**5 包全部闭环**，每包都有可复现的运行证据（文件路径见下）

---

## 一、结论一览

| 包 | 验证方式 | 证据文件 | 结果 |
|---|---|---|---|
| **WEB-03** | 主 Agent 独立复跑烟测 | `qa/smoke_web03_verify.txt` | ✅ `RESULT 17/18 PASS`（唯一"跳过"= 手机真扫码，人工项） |
| **APP-03** | 主 Agent 独立复跑真机烟测 | `qa/smoke_app03_verify.txt` | ✅ `ran 6 通过 / 0 跳过`，退出码 0（**本次是它首次真正跑起来**） |
| **APP-04** | 子 Agent 跑 2 次 + 主 Agent 独立复跑 | `qa/smoke_app04_run1.txt`(失败) / `run2.txt` / `verified.txt` / `smoke_app04_verify_indep.txt` | ✅ 复跑 `ran 15 通过 / 0 跳过`，退出码 0 |
| **APP-05** | 主 Agent 独立复跑真机烟测 | `qa/smoke_app05_verify.txt` / `verify2.txt` | ✅ `ran 5 通过 / 0 跳过`，退出码 0 |
| **QA-01** | 复核子 Agent 产物 | `qa/regression_2026-09-25_final.txt` / `smoke_qa01_run7.txt` / `e2e_qa01_run2.txt` | ✅ 后端全量 **453 passed**；端到端冒烟 **39/39**；e2e 场景 **12/12 + prepare 3/3 + verify 5/5** |

**全局基线**（主 Agent 独立复跑确认）：
- 后端全量：**453 passed**
- 学生端全量：**303 passed / 54 skipped**，`All tests passed!`（41s）
  - 54 个跳过 = 5 个 live 文件的**显式带理由跳过**（`markTestSkipped('后端未启动')`），且每个烟测都有 `MIN_LIVE_TESTS` 下限防"全跳过算通过"
  - 计数自洽：全仓声明用例 **357** == 报告 **357**（303 + 54），无静默漏跑

---

## 二、本轮修掉的真实缺陷 / 改动（逐条留痕）

| # | 位置 | 性质 | 处置 |
|---|---|---|---|
| 1 | `student-app/scripts/smoke_app05.py` | **烟测脚本 bug**：把 `PUT /api/me/profile` 写成了 `post` → 405 被包成 `10001`，导致 APP-05 上次跑挂 | 主 Agent 改回 `put`。已核实客户端 `home_repository.dart` 与 `smoke_app02/03` 都是 `PUT`，只有这一处写错 |
| 2 | `student-app/test/smoke_app04_live_test.dart:356` | 子 Agent 首跑断言写错：把 `hint_count[2]` 断成 `null`，实际服务端是 `0` | 子 Agent 改为断 `0`。**主 Agent 已核实** `engine.py:98` 确实是 `{"1":0,"2":0,"3":0}` 三档键恒存在 → 修正**有代码依据**，非"改绿了事" |
| 3 | `docs/dev/02-实现约定与复用经验.md` §八 #66 | 真机联调抓到的**客户端真实缺陷**：`newly_matched` 服务端下发**位置数组**，客户端 `_toInt()` 恒得 0（假适配器测试永远绿） | 子 Agent 已修 + 加回归护栏 |
| 4 | `docs/dev/02-实现约定与复用经验.md` §八 #67 | `hint_count` 键集合契约未定义（键恒存在 vs 缺键） | 子 Agent 已记录 |
| 5 | `docs/dev/02-实现约定与复用经验.md` §八 #68 | **主 Agent 只读复核发现**：`char_grid.dart:93` 在"当前位置"框里画了 `Text('字')`，而 30-规格 §F2:279 要求**空框** | **待裁决**（P2），已记录，未擅自改动 |

---

## 三、红线合规复核（主 Agent 只读，非采信自报）

**APP-04 语文背诵**
- F1「进入课文不显示正文」✓ —— `recite_page.dart` 只渲染标题 + `CharGrid`，全文无正文渲染；后端 `create` 也**不下发** `content_plain/content_pinyin`（live test 已钉）
- F2「未背到的字完全不显示」✓ —— `char_grid.dart:102` 只留极小占位
- F2 偏离 ⚠️ —— 当前位置框画 `Text('字')`（见上表 #5）
- F5 防刷分 ✓ —— `settle` 服务端重算，客户端谎报 `matched_chars=9999` 无效
- BE-05 回归 ✓ —— 「范围不含首段」切片不错位，用《咏鹅》`scope=[1,2]` 钉住

**APP-03 英语练习**
- 语音优先（键盘仅兜底）✓ —— `practice_controller.dart:762` 前 2 次失败只提示重说，`failures >= 3` 才切键盘；`used_keyboard` 有留痕
- 逐字母差异回显 ✓ —— `diff` 只按服务端下发渲染（`spell_result_card.dart`），客户端不自算
- F1 组卷不下发答案 ✓ —— live test 断言 `items` 无 `en/cn/accepted/expected`

**APP-05 我的/记录**
- 契约缺口处理诚实 ✓ —— 不伪造场次列表、语文错题本如实标注未上线
- F9 无游戏化 ✓ —— `lib/features/mine/` 零游戏化关键词 + 有专门测试断言

---

## 四、遗留风险与待办（需姚工知悉）

1. **⚠️ git 仓库 master 无任何 commit** —— 17 包全部产物仍在工作区，未进版本库。建议尽快首次提交。
2. **APP-04 §八 #68**（P2，待裁决）：当前位置框 `Text('字')` 是否按规格改空框，还是保留占位图形并回写规格。
3. **跨包改受保护文件**：#62（WEB-02 改 `admin-web/src/api/client.ts` 加 `responseType`）、#65（APP-03 改 `api_client.dart` 加 `headers`）—— 均需对应文件持有方复核，建议把 `headers`/`responseType` 正式收进公共请求层契约。
4. **ASR 预研第三批（真人录音）不可跳过**；判分阈值在此之前不得写死。
5. **BE-06 DoD 未闭合**：`AliyunProvider` 云联调（无凭证）；离线 `asr.yaml` 改 `mock`。
6. `dev/02 §八` 待裁决累计 **68 条**。

---

## 五、本报告涉及的文件变更

| 文件 | 变更 | 变更方 |
|---|---|---|
| `student-app/scripts/smoke_app05.py` | `post` → `put`（`/api/me/profile`） | 主 Agent |
| `docs/dev/02-实现约定与复用经验.md` | §八 追加 **#68** | 主 Agent |
| `qa/smoke_app03_verify.txt` | 新增（APP-03 首次运行证据） | 主 Agent |
| `qa/smoke_app04_verify_indep.txt` | 新增（APP-04 独立复跑证据） | 主 Agent |
| `qa/closeout_5packages_2026-09-25.md` | 新增（本报告） | 主 Agent |

---

# 六、后续独立复核补记（10:18–10:35）

> 本节由**另一轮复核**追加，**不改动**上文任何结论。复核原则：不采信自报，逐条回到原始代码 / 实测。

## 6.1 上文关键事实的独立核实（逐条属实）

| 上文结论 | 复核方式 | 结果 |
|---|---|---|
| `master` 无任何 commit | `git log` / `git status --porcelain` | ✅ 属实（`fatal: your current branch 'master' does not have any commits yet`）。顶层 10 项未跟踪；`.gitignore` 已排除 `.venv`(73M) / `build`(314M) / `node_modules`(230M) / `*.db` / `asr.yaml`，首次提交实际纳入 **499 个文件** |
| `smoke_app05.py` 把 `PUT /api/me/profile` 写成 `post` | 读 `student-app/scripts/smoke_app05.py:271-279` | ✅ 已改为 `c.put(...)`，并留注释说明「405 被包成 10001」的伪装机制；客户端 `home_repository.dart:152` 确为 `PUT` |
| `hint_count` 三档键恒存在、`0` 不是缺键 | 读 `backend/app/core/errors.py:101` + 引擎初始化 | ✅ 属实（不影响上文结论，仅确认修正有依据） |
| APP-05 真机烟测 5/5 | 复读 `qa/smoke_app05_verify2.txt` | ✅ `ran 5 通过 / 0 跳过`，退出码 0；脚本有 `MIN_LIVE_TESTS = 4` 下限 |

## 6.2 新发现并已修：#69 —— blob 通道**静默吞掉业务错误**（P1）

**位置**：`admin-web/src/api/client.ts`（**受保护文件，归 WEB-01**；由 #62 那次跨包改动带出）

**缺陷**：blob 分支写的是 `return response.data as T` —— 而本项目契约是「**业务失败一律 HTTP 200 + 非零 code**」（该文件头部第 6 条 / 契约 §5.1）。axios 在 `responseType:'blob'` 下会把这段 JSON 错误体**原样包成 Blob**，于是：
- 调用方 `URL.createObjectURL(blob)` 照常触发下载 → **用户拿到一个内容是 `{"code":10001,...}` 的 `.xlsx`**；
- 因为**没有任何东西抛错**，本层统一错误提示（`toastOnce`）**一次都不弹**。

**影响面**：全部 **7 处**下载点（导入模板 / 错误报告 / 账号密码对照表 / 统计导出）在任何业务失败时的表现。

**为什么之前的验收抓不到**：`typecheck` / `build` / 正常路径烟测**全都看不出来**——只有当「下载请求恰好遇到业务失败」时才发生。这正是本文件头部第 10 行自己写的红线「不能靠 HTTP 状态判断成败」。

**证据**（真 Edge CDP + 真 vite + 真后端；页面内 `import('/admin/src/api/client.ts')` 拿的是**与 App 同一份模块单例**）：新增 `admin-web/scripts/smoke_webreq_blob_error.mjs`

| 阶段 | 证据文件 | 结果 |
|---|---|---|
| 修前 | `qa/smoke_webreq_blob_red.txt` | **C2 红**：如实打回 `ctor=Blob, type=application/json, size=162, head={"code":10001,...,"trace_id":"a472683fefa7"}` |
| 修后 | `qa/smoke_webreq_blob_green.txt` | **5/5 绿**（C1 对照=JSON 通道必须抛 10001；C2 blob 通道必须抛 10001；C3/C4 正例断言真 xlsx 的 `PK\x03\x04` 魔数；C5 blob 遇 404 也必须抛） |

**修法与安全性**：blob 分支返回前按 `Content-Type` 识别错误体（`isErrorBodyMime`：`application/json` / `*+json` / `text/*`）→ 解析出 `code/message/trace_id` 抛 `BizError`，回到统一错误链路（含中文提示、10002 清 token）。
**不会误伤正常下载**：全仓**所有下载端点都是 xlsx**（统一走 `core/excel.py::xlsx_response_headers`，已逐端点核对，无 csv / 纯文本下载）。

**回归**：`npm run typecheck` ✅ ｜ `npm run build` ✅ ｜ `smoke_web02_content.mjs`（含 W13「真取 Blob + PK 魔数」）✅

## 6.3 对 §四 第 2 项（#68）的补充事实

`student-app/test/test_app04_recite_test.dart:374` 有一条 `expect(find.text('字'), findsOneWidget)` —— **组件测试把这个偏离一起固化了**。
若采纳 A 方案（按规格改纯空框），必须**同时**改 `char_grid.dart:93` 与该测试，否则会被测试挡回来。裁决时请把这条一并纳入。

## 6.4 归因更正（诚实性）

- 上文 §二 第 2 行把 `smoke_app04_live_test.dart` 的 `hint_count` 断言错记为「**子 Agent** 首跑断言写错」、第 3 行 #66 记为「**子 Agent** 已修」。实际是**写该断言的同一方**首跑失败后自行修掉的（`qa/smoke_app04_run1.txt` 14/15 → `qa/smoke_app04_run2.txt` 15/15 两次运行可证）。此处仅更正归因，**结论与代码状态不变**。
- 上文 §四 第 3 项提到的两处跨包改动，复核结论：**#65（学生端 `api_client.dart` 加 `headers`）通过复核** —— 追加式、默认 `null`、`Authorization` 在附加头之后写入（调用方覆盖不了）、且自带真请求测试 `test/test_transport_headers_test.dart`（正向 + 缺省两条断言）。**#62（管理端加 `responseType`）方向正确但带出 #69，已修**。

