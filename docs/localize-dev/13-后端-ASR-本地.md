# 13 - 批次E · 后端 ASR 本地化（预研门 → LocalProvider）

> 依赖：批次 A。**全程最重，先预研后接线，两道门不过不准上线。**
> 契约依据：01 §1.3（ASR order 映射）、§3（模型）、§6（懒加载）、§7.2（预研门）。

## 1. 依赖安装

```bash
cd backend && env -u HTTP_PROXY -u HTTPS_PROXY .venv/Scripts/pip.exe install sherpa-onnx vosk -i https://pypi.org/simple
pip show sherpa-onnx vosk   # 实测版本写死进 requirements.txt（sherpa-onnx 与批次 B 同版本）
```
Windows 无 wheel → 停下上报（不准换 torch 系）。

## 2. 模型下载（并入 `scripts/download_models.py`，`--only asr`）

- `backend/models/asr/vosk-model-small-en-us-0.15/`（zip，40MB）
- `backend/models/asr/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/`（int8 onnx 全套）
- 下载源顺序照 01 §3。

## 3. 预研门（先跑，过门才允许接线）

### 3.1 脚本 `backend/scripts/_asr_local_probe.py`

- 输入：`qa/audio/*.wav`（71 个，命名 `<词>__spell|__syllable|__whole.wav`，词名即文件名前缀）。
- **引擎 A（Vosk，字母）**：加载模型 + grammar `["a","b",...,"z"]`（RecognizeSet/grammar 约束，写死 26 字母小写集合），跑所有 `*__spell.wav`（`__syllable.wav` 音节文件**不参与**拼写门，只跑 spell）：
  - 指标 = 识别序列与目标词的**最长公共子序列长度 / 目标长度**，逐文件打印，汇总均值；
  - 门线：均值 ≥ 0.90。
- **引擎 B（sherpa streaming zipformer，中文）**：跑所有 `*__whole.wav` 里**中文含义**判定不可行（whole 是英文朗读），改为：
  - 用 sherpa 引擎转写 `*__whole.wav`，指标 = 与目标词归一化（小写去空格）的**编辑距离相似度 ≥ 0.8 的比例**，门线 ≥ 80%；
  - **中文识别抽检**：另有 2026-10-11 真机会话验证过 en2zh 链路，但本地中文引擎无现成中文 wav —— 从 qa 中挑 3 个 whole 文件用 PC 播放 + 手录不可行，改为：**用批次 B 的本地 TTS 合成 3 条中文 wav（"苹果""香蕉""早上好"）喂给引擎 B**，转写命中即算中文通路 OK（脚本内置这个自检）。
- stdout 打印逐文件明细 + 两个门线判定结论 `SPELL_GATE: PASS/FAIL`、`ZH_GATE: PASS/FAIL`。

### 3.2 门判定

- 双 PASS → 继续本批次接线；
- 任一 FAIL → **停下上报实测明细**（逐文件表 + 模型版本 + 音频清单），等姚工裁决（可能结论：换备选模型 / ASR 暂缓本地化）。**不准**带着 FAIL 门硬接。

## 4. 新增 `backend/app/services/asr/local_provider.py`

实现 `base.ASRProvider` 的全部抽象方法（**先读 `base.py` 的抽象签名与 aliyun_provider 的实现套路**，签名逐字对齐）。行为矩阵：

| 任务 | 引擎 | 说明 |
|---|---|---|
| `spell_zh2en`（逐字母拼读） | Vosk + 26 字母 grammar | 每轮 end → 转写序列（如 "a p p l e"）→ 交给现有 `_spell_feed` 逐格判定（**与云端同样的转写口径**：字母序列，空格/逗号分隔均可，`_spell_feed` 现有清洗逻辑兼容即不改；不兼容则只许在 provider 层输出规整为空格分隔小写字母序列） |
| `spell_en2zh` / `recite`（中文转写） | sherpa streaming zipformer zh-en | 流式：`push_audio` → partial 回调；end → final。回调解耦与 aliyun_provider 相同（on_partial/on_final 线程投递到事件循环，照 aliyun_provider 的既有桥接写法） |

要点（写死）：
- 模型单例 + 双检锁（契约 §6）；Vosk Recognizer 与 sherpa stream **每会话新建**，会话 close 释放。
- `start_session` 里模型缺失/加载异常 → 抛 `ASRError(50006, ...)`（ASRRouter 会当建连失败落下一顺位——hybrid 自动回云端，local 明确报错）。
- `asr.yaml` 无需为 local 增加配置段；provider 从 `capability` 模块拿模型路径（`_MODEL_SPECS` 登记 asr 两模型）。
- 批量通道（`finish`/`close` 的 HTTP 提交语义）不适用本地：本地 provider 的 `close()` 直接产出 final（同步转写完再返回），**保持 base.py 契约的对外时序不变**。
- 日志格式与 aliyun_provider 一致（task_type、status、cost）。

## 5. 注册 `app/services/asr/factory.py`

```python
from app.services.asr.local_provider import LocalProvider
PROVIDERS = { "aliyun":..., "volcengine":..., "mock":..., "local": LocalProvider }
```
只加这一行 import + 一个 dict 条目，其他不动。

## 6. 测试 `backend/app/tests/test_local_asr.py`（≥8 用例，Mock 引擎）

单测 monkeypatch LocalProvider 的引擎调用（不起真模型）：

1. factory 能创建 "local"（型号注册生效）；
2. hybrid：local start_session 抛 ASRError(50006) → ASRRouter 落 aliyun 成功（用 MockProvider 当 aliyun 或直接用现有测试的 session 套路）；
3. local 模式：local 失败 → 整场报错（无顺位可落）；
4. spell_zh2en：喂 PCM（静音或合成正弦即可，引擎被 mock）→ mock 返回 "a p p l e" → 现有 letter 判定链路产出 5 个 letter 帧（与云端同链路）；
5. en2zh：mock 返回中文 final → 不产出 letter/verdict（网关不判分，与云端同契约）；
6. 模型缺失 → start_session 抛 50006（构造：`_MODEL_SPECS` 指向不存在路径）；
7. 懒加载：不 start 时 `engine_ready("asr")` 不加载模型（文件存在性判定）；
8. `asr_logs.provider` 记录 "local"（走现有日志落库链路断言）。

## 7. DoD

- [ ] 预研双门 PASS（或按 §3.2 上报裁决）；
- [ ] `pytest app/tests/test_local_asr.py -q` 全绿；全量 pytest 无新增失败；
- [ ] 真机冒烟（并入批次 F）：hybrid 下拼读/看英文背中文日志出现 `provider=local`（或明确上报切换原因）；
- [ ] 上报：两个模型体积、预研明细表、实测首字延迟与整句耗时。

## 8. 禁止事项

- 禁改 `_spell_feed`/判分引擎/`session_manager` 的协议层（只许 §1.3 的 order 覆盖与 provider 注册）；
- 禁改 `base.py` 抽象签名；
- 禁止 torch / funasr(python 包) / paddle 系依赖（sherpa-onnx + vosk 之外的新依赖 → 先上报）；
- 预研门 FAIL 不准接线。
