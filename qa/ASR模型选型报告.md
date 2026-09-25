# ASR / TTS 模型选型对比报告

| 项目 | 内容 |
|---|---|
| 报告版本 | v1.0 |
| 执行日期 | 2026-09-23 |
| 上游文档 | `docs/dev/05-ASR预研方案.md`、`qa/ASR预研报告.md` |
| 触发 | 姚工提供百炼「模型市场」截图，要求选定模型 |
| 原始数据 | `qa/asr_model_compare.csv`、`qa/asr_negative_test.csv` |

---

## 一、结论（先看这个）

### 1.1 推荐选型

| 场景 | 推荐模型 | 类型 | 首字延迟 | 依据 |
|---|---|---|---|---|
| **语文背诵（逐字点亮）** | **`qwen-audio-3.1-asr-flash-streaming`** | WebSocket 流式 | **340–354ms** | 中文 2/2 全对；中间包 8–10 个，粒度足够点亮；延迟全场最低 |
| **英语拼读判分** | **`fun-asr-flash-2026-06-15`** | HTTP 非流式 | 323ms（P50） | **负样本 4/4 忠实，不掩盖错读**；有逐词时间戳 |
| **英语说单词（整词）** | `fun-asr-flash-2026-06-15` | HTTP 非流式 | 同上 | 同一模型覆盖，减少维护面 |
| **拼读疑难二次校验**（可选） | `qwen-audio-3.0-asr-flash` | HTTP 非流式 | 895ms（P50） | 与 fun 结论不同时做交叉验证 |

### 1.2 明确不推荐

| 模型 | 原因 |
|---|---|
| ⛔ **`qwen3-asr-flash`（原方案主力）** | **会把学生的错读"脑补"成正确单词**（`G L A S S` → `CLASS`、`A B P L E` → `APPLE`）。放到判分场景是**教学事故**：学生把错的记牢，系统还一路给绿灯 |
| ⛔ `qwen3-asr-flash-realtime` | 本账号下 `task-failed`，不可用 |
| ⛔ 所有 `paraformer-*` / `sensevoice` | 本账号不可用 |

> **这条是本轮最重要的修正**：`qwen3-asr-flash` 在"正样本"上准确率最高（95%），
> 但那个数字是**虚高**——它不是听得准，是**猜得准**。判分要的不是"猜得准"，是**忠实**。

---

## 二、测试方法与数据

### 2.1 样本

| 项 | 内容 |
|---|---|
| 正样本 | `qa/audio/` 现有 20 条干净拼读音频（omni 质检通过） |
| **负样本** | **`qa/audio_neg/` 6 条"故意念错"音频**（TTS 合成 + omni 质检确认实际内容） |
| 中文样本 | `qa/audio_cn/` 2 段课文（古诗 +《背影》片段） |
| 音频格式 | TTS 合成，24kHz → 重采样 16kHz / 16bit / 单声道 |
| 局限 | **仍是 TTS 音频，非真人录音**。绝对值偏乐观，但**模型之间的相对排序有效** |

### 2.2 表一：非流式模型 · 正样本准确率

| 模型 | 准确率 | P50 延迟 | P95 延迟 | 逐词时间戳 |
|---|---|---|---|---|
| `qwen3-asr-flash` | 19/20 = **95.0%** | 433ms | 620ms | ❌ |
| `qwen-audio-3.0-asr-flash` | 16/20 = 80.0% | 895ms | 10800ms | ✅ |
| `fun-asr-flash-2026-06-15` | 17/20 = 85.0% | **323ms** | 1034ms | ✅ |

> `qwen-audio-3.0-asr-flash` 的 P95 异常（10800ms）来自个别请求超时重试。

### 2.3 表二：负样本忠实度（**本报告的核心依据**）

负样本做法：TTS 念出**错误的**字母序列（如目标 `CLASS` 但实际念 `G L A S S`），
用 omni 模型独立质检确认音频真实内容，再看各 ASR 输出什么。

| 模型 | 忠实听到真实内容 | **掩盖（判成正确词）** |
|---|---|---|
| `qwen3-asr-flash` | 2/4 | **2/4** 🔴 |
| `qwen-audio-3.0-asr-flash` | **4/4** | **0** ✅ |
| `fun-asr-flash-2026-06-15` | **4/4** | **0** ✅ |

原始证据：

| 目标 | 实际念出 | qwen3-asr-flash | qwen-audio-3.0 | fun-asr-flash |
|---|---|---|---|---|
| CLASS | **G L A S S** | `CLASS` 🔴 | `GLASS` ✅ | `GLASS` ✅ |
| APPLE | **A B P L E** | `APPLE` 🔴 | `ABPLE` ✅ | `ABPLE` ✅ |
| CAT | **B A T** | `BAT` ✅ | `BAT` ✅ | `BAT` ✅ |
| DOG | **D O B** | `DOB` ✅ | `DOB` ✅ | `DOB` ✅ |

**机制解释**：`qwen3-asr-flash` 有强英语语言先验，会把听不清的字母序列"纠正"成
最可能的真实单词。念对了很有用（纠正发音瑕疵），念错了就是灾难。

> ⚠️ **样本量说明**：负样本质检通过后仅 4 条（BUS、SCHOOL 两条音频本身不合格）。
> 这个样本量**不足以给出精确的掩盖率**，但足以证明**该行为机制真实存在**。
> 建议在真人录音复测时扩大负样本（≥20 条）。

### 2.4 表三：流式模型（语文逐字点亮可行性）

| 模型 | 首字延迟 | 中文课文准确率 | 中间包数 | 拼读负样本 |
|---|---|---|---|---|
| **`qwen-audio-3.1-asr-flash-streaming`** | **340 / 354ms** | 2/2 = 100% | 8–10 | 2/3 忠实 |
| `qwen-audio-3.0-asr-flash-streaming` | 374 / 505ms | 2/2 = 100% | 8–10 | 2/3 忠实 |
| `fun-asr-realtime` | 366 / 518ms | 2/2 = 100% | 8–10 | 2/3 忠实 |
| `qwen3-asr-flash-realtime` | ❌ task-failed | — | — | — |

中文识别原文核对：

- `床前明月光，疑是地上霜。举头望明月，低头思故乡。` → **三个模型全部完全正确**
- `我与父亲不相见已二年余了，我最不能忘记的是他的背影。` → **三个模型全部完全正确**

> **中间包是累积式的**（每个包包含从开始到当前的全部文本），可直接支撑"逐字点亮"：
> 每收到一个新包，把新出现的字点亮即可。

三个流式模型在 `CLASS/GLASS` 上**全部被纠正**（C↔G 是全场共性混淆，与非流式版表现不同），
在 `APPLE/ABPLE`、`CAT/BAT` 上忠实。

---

## 三、模型 id 对照（截图上没有，实测才拿到）

| 截图展示名 | 实际 model id | 调用方式 |
|---|---|---|
| Qwen-Audio-3.1-ASR | `qwen-audio-3.1-asr-flash-streaming` | WebSocket |
| Qwen-Audio-3.0-ASR | `qwen-audio-3.0-asr-flash` | HTTP |
| Qwen-Audio-3.0-ASR（流式） | `qwen-audio-3.0-asr-flash-streaming` | WebSocket |
| Fun-ASR实时语音识别 | `fun-asr-realtime` | WebSocket |
| Fun-ASR-Flash | `fun-asr-flash-2026-06-15` | HTTP |
| ~~Qwen-Audio-3.1-ASR-Message~~ | 未确认（`qwen-audio-3.1-asr-message` 不存在） | — |
| Qwen-Audio-3.1-TTS | `qwen-audio-3.1-tts-flash`（模型存在，接口待确认） | — |

### 3.1 HTTP 调用的关键坑

`qwen-audio-3.0-asr-flash` / `fun-asr-flash` **必须传 `parameters.format`**，否则报
`UNSUPPORTED_FORMAT: format is empty`：

```json
{
  "model": "qwen-audio-3.0-asr-flash",
  "input": {"messages": [{"role": "user", "content": [{"audio": "data:audio/wav;base64,..."}]}]},
  "parameters": {"format": "wav"}
}
```

响应结构也和 `qwen3-asr-flash` **不同**（解析代码要分别处理）：

```json
{"output": {"output": {"sentence": {
    "text": "C L A S S. ",
    "words": [{"text": "C", "begin_time": 320, "end_time": 680}, ...],
    "sentence_end": true
}}}}
```

### 3.2 WebSocket 协议要点

```json
// 端点
wss://dashscope.aliyuncs.com/api-ws/v1/inference

// 1) 建连后发 run-task
{"header": {"action": "run-task", "task_id": "<32位>", "streaming": "duplex"},
 "payload": {"task_group": "audio", "task": "asr", "function": "recognition",
             "model": "qwen-audio-3.1-asr-flash-streaming",
             "parameters": {"sample_rate": 16000, "format": "pcm"}, "input": {}}}

// 2) 收 task-started 后推二进制音频帧（16kHz/16bit/单声道，100ms = 3200 字节）
// 3) 发 finish-task
{"header": {"action": "finish-task", "task_id": "<同上>", "streaming": "duplex"},
 "payload": {"input": {}}}
```

事件：`task-started` → `result-generated`（多次，取 `payload.output.sentence.text`）→ `task-finished` / `task-failed`。

---

## 四、成本估算

按 40 人规模、每人每天 20 词（说词 + 拼读两步）+ 1 段课文：

| 项 | 用量 | 单价 | 日成本 | 学期成本（≈100 天） |
|---|---|---|---|---|
| 英语（fun-asr-flash） | 1600 次 × ≈2s = 3200s | 0.00022 元/秒 | ≈0.70 元 | ≈70 元 |
| 语文（流式） | 40 段 × ≈180s = 7200s | 0.00033 元/秒 | ≈2.38 元 | ≈238 元 |
| **合计** | | | **≈3.1 元/天** | **≈310 元/学期** |

> 单价以模型市场页面公示为准，实际计费口径（按 token 还是按时长）以账单为准。**结论：成本不构成约束。**

---

## 五、对设计文档的影响

| # | 文档 | 原内容 | 应改为 |
|---|---|---|---|
| 1 | 需求 §9.1 模型表 | Paraformer 实时版 / Fun-ASR 实时版 | `qwen-audio-3.1-asr-flash-streaming`（语文）+ `fun-asr-flash-2026-06-15`（英语） |
| 2 | 契约 §9.2 `aliyun.model` | `qwen3-asr-flash` | 按场景双模型；拼读**禁用** `qwen3-asr-flash` |
| 3 | 契约 §9.2 | 语种参数按题型切 | 仍需切，但**拼读的 `zh` 结论要在新模型上重验**（本轮新模型用 `zh` 未复现旧效应） |
| 4 | 10b BE-06 | AliyunProvider 单实现 | 拆 `streaming` / `batch` 两种调用路径 |
| 5 | 需求 §6.2 判分 | 依赖 ASR 输出 | **新增约束**：判分模型必须是"忠实型"，禁止用会做语言纠正的模型 |
| 6 | 05-ASR预研方案 | 语种对照 | 新增"模型忠实度"作为独立的选型维度 |

---

## 六、仍未解决 / 下一步

| # | 事项 | 说明 |
|---|---|---|
| 1 | **真人录音复测** | 本轮全是 TTS 标准音。**模型相对排序可信，绝对准确率不可信** |
| 2 | 扩大负样本 | 负样本需 ≥20 条，才能给出可信的"掩盖率" |
| 3 | `qwen-audio-3.1-asr-flash-streaming` 详细信息 | 是否支持 `language_hints`、热词、逐字时间戳，需查其专属 API 文档 |
| 4 | TTS 接口 | `qwen-audio-3.1-tts-flash` / `cosyvoice-v3.5-plus` 存在但报 `url error`，需查文档或控制台开通 |
| 5 | 噪声环境 | 完全未测 |
| 6 | C↔G 混淆 | 全场共性，需进谐音表高优先级 |

---

## 附：本轮脚本

| 脚本 | 用途 |
|---|---|
| `qa/probe_models.py` | 探测候选模型 id 与接口形态 |
| `qa/asr_model_compare.py` | 非流式三模型正样本对比 |
| `qa/asr_negative_test.py` | **负样本忠实度测试**（核心） |
| `qa/asr_stream_test.py` | 流式模型可用性与首字延迟 |
| `qa/asr_stream_verify.py` | 流式负样本 + 中文课文识别 |
