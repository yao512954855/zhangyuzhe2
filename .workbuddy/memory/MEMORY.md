# 背记系统 — 项目长期约定（精简版，细节见 docs/dev/02 与 .workbuddy/memory/ 日志）

> 中小学英语/语文背诵系统（局域网）。只有「管理者」「学生」。Flutter / Vue3+TS+ElementPlus / FastAPI。DB SQLite(WAL) 不用 ORM，pypinyin==0.51.0 锁死。端口 8810、UDP 9527、管理端 {host}:8810/admin。

## 文档与优先级
`docs/`：需求 v0.6 · 数据库(DDL 冻结) · 接口 · UI 规范(§7=APP 验收)。
`docs/dev/`：00-总纲(§10 状态表) · 01-契约冻结(必读) · 02-实现约定(§八 待裁清单，每包必追加)。
**优先级：契约冻结 > 接口设计 > 数据库设计**；契约冲突停下报告不绕过。
`backend/asr.yaml` 被 .gitignore 排除；百炼 key 已填（09-28，`asr_ready=true`）。⛔禁用 `qwen3-asr-flash`；仲裁仅 空结果/fuzzy/申诉，严禁进主链路。

## 关键业务约定
- 导入必带 学科/年级/学期；有答题记录只能停用不能删；语文粒度按字；判分模型必须单独测负样本忠实度
- 语音优先硬约束：键盘仅兜底、第 3 次失败才降级、留痕 `used_keyboard`；学生自主注册免审；App 必须 UDP发现+扫码+手输
- **打分口径**（需求 §6.4）：单题 100 起扣、答错 0；重试−10/键盘兜底−15/提示−20/−40/−60（settings 可调）；场次分=非全空项单题分平均；`score` 与 `accuracy` 是两个指标（键盘全对=85分/正确率100%）。评级 ≥90优秀/≥80良好/≥60加油
- **语文结算页回看=全文着色**（#108，UI §7.8）：背出=正文色、漏字=error色+errorLight底+w600+图例；全文来自 `reciteTextProvider`，`fullChars.length != settle.totalChars` 就退回逐字清单；判分口径唯一来自服务端 `missed_detail`
- 判定口径与结算统计是同一契约两头，改一头必须回看另一头（#97 教训：合并单轮后客户端只发 step=1，`valid` 非空即算作答完成）

## 实现约定（详见 dev/02 §一）
- 接口一律 `ok()`/`page()`，业务失败=200+非零 code；新后端接口只放 `app/api/xxx.py`（不许改 main.py）；管理端请求只走 `api/client.ts`、页面只加 `router/routes/xxx.ts`
- 下拉一律走 options 接口（`/api/classes/options` 等），⛔禁用分页接口传大 pageSize 绕分页
- DDL 冻结=已发布迁移不可改，新增表走新迁移文件，⛔ 001_init.sql 永不改
- 全量 pytest 前必须先停 dev 后端（UDP 9527 撞车 → BE-08 假红，#104）
- 管理端/学生端全量回归口径：后端 pytest 624 passed；学生端 analyze 0 error + test ~414 passed/48 skipped（后端不在线会多 skip，看数别慌）

## 环境与踩坑（高频）
- Flutter 在 `D:\2install\flutter\flutter_windows_3.47.5-stable\flutter`；**bash 直调 flutter 必崩 → 用 `scripts/flutter.sh`**（student-app 下 `bash ../scripts/flutter.sh`）；⚠️ `flutter test` 不许传 `-d`；输出重定向到文件别用 `| tail`
- **长跑 dev 后端**：`cd backend && env -u PYTHONPATH .venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8810`，后台任务启动（能跨会话存活）；没开 --reload → 改码必须重启；杀进程用 PS `Stop-Process -Id <pid> -Force`（taskkill 被拦）；后端同时占 UDP 9527
- ⚠️ 后端改码后旧进程返回的不是 404 而是 `10001`，极易误判
- 沙箱 safe-delete：删除重定向回收站、批量守卫拒深目录/大目录；`SystemExit(1)` 杀应用进程 → 真机烟测一律 `env -u PYTHONPATH`；vite 构建输出到全新 outDir 别原地清
- 下载走阿里云镜像（`~/.gradle/init.gradle` 已配）；`repo.maven.apache.org` 会卡死
- ⛔ 绝不在 build() 里调 Navigator（#98，_debugLocked 死页）；异步按钮 `_busy` 必须有 finally；riverpod 3 无 valueOrNull（用 `asData?.value`）；Notifier.build() 里不能同步读 state
- 学生端清栈用 `pushNamedAndRemoveUntil`，别 pop（黑屏）；清栈前 await 退场守卫 400ms（#95）
- 真机 UI 用 `adb shell uiautomator dump` 读语义树（别吞 dump 报错，旧文件会骗人）；渲染类缺陷用截图+像素行剖面（dpr=2.75）
- 音频链路 PC喇叭→手机麦不可用于验收判分；验语文全对用 `backend/scripts/_cn_trace.py`
- C 盘满会让 flutter test 假卡死：先 `df -h`，`TMP/TEMP` 指到 D 盘，删可再生 `build/test_cache`，用未改动用例区分环境vs代码
- UDP 9527 全局互斥 → 任何真起 uvicorn 的测试不能并行

## 当前状态（2026-10-13 更新）
一期 38/40 验收；S2 简化版真机验收闭环（10-01/10-11 双轮修复）。**本地化（localize-dev）三引擎全接线**：TTS=piper+melo（melo 必须 lexicon+dict_dir、data_dir 只认 espeak-ng-data）、OCR=rapidocr、ASR=Vosk字母+**SenseVoice small int8**（契约 01 v1.0.2：zipformer bilingual 预研 FAIL 换型）。模式三态 local/hybrid/cloud，管理端「能力设置」页切换，CAP_TTS/OCR/ASR env 锁定优先。
⚠️ 高频坑：①numpy 线性插值降采样无抗混叠会毁识别→按真实率喂 accept_waveform；②glob("*encoder") 尾锚匹配不到 onnx；③测试假设本地引擎不存在的旧用例必须加 `CAP_ASR/CAP_TTS=cloud` 部署锁（否则 hybrid 走真本地引擎全崩）；④类方法要 patch 类不是模块；⑤qa/audio 旧翻录夹具不可信（云端 fun-asr 也翻车，pen 内容错乱）。
后端 pytest 全绿 325；`scripts/smoke_local_asr.py` = hybrid 端到端冒烟（TTS/字母/中文转写/判分）PASS。
⚠️ 本地化改动未 commit；backend/models/ 不入库（.gitignore 已排）。
⚠️ adb.exe 是 Windows 程序，install 等传文件参数必须用 `D:/` 风格路径，不认 MSYS 的 `/d/`。adb 在 `D:/2install/androidstudiotool/sdk/platform-tools/`。

## 待办
- 🔴 语文链路真实运行 0 次（流式云 ASR 从未真调用）→ 真机跑一次语文背诵才算闭合 C1/C9
- 🔴 NF-03 延迟未计时；ASR 预研第三批（真人录音）不可跳过，判分阈值此前不得写死
- BE-06 欠语文流式；轮换明文出现过的百炼 key（S1）；C2/C4–C8 人工项
