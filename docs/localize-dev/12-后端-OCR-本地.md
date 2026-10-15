# 12 - 批次C · 后端 OCR 本地化（RapidOCR）

> 依赖：批次 A 完成。可与批次 B 并行（不同文件）。
> 契约依据：01 §1.2（行为矩阵 + **同构 map 适配**）、§7.1（手写门）。

## 1. 依赖安装

```bash
cd backend && env -u HTTP_PROXY -u HTTPS_PROXY .venv/Scripts/pip.exe install rapidocr-onnxruntime -i https://pypi.org/simple
pip show rapidocr-onnxruntime onnxruntime   # 实测版本写死进 requirements.txt
```

- onnxruntime 版本必须与批次 B 写死的**同一个版本**（两个包共同依赖，冲突时以能同时满足两者安装成功的最高版本为准，并上报）。
- 模型包内置，无单独下载。安装后 `python -c "from rapidocr_onnxruntime import RapidOCR"` 即算可用。

## 2. 新增 `backend/app/services/ocr_local.py`（本地 OCR 客户端）

```python
"""本地 OCR（RapidOCR）—— 输出与阿里云 RecognizeHandwriting 同构的 map
（localize-dev/01 §1.2），让 dictation_service 解析层零改动。"""

class LocalOcrClient:
    """接口与 dictation_service._OcrClient 完全一致：recognize(image_bytes) -> dict。"""

    def __init__(self) -> None: ...
    def recognize(self, image_bytes: bytes) -> dict[str, Any]: ...
```

适配规则（写死）：
- RapidOCR 调用：`engine(image_bytes)` → `[[box, (text, score)], ...]`。
- 每个结果 → 块 `{"word": text, "x": int(box[0][0]), "y": int(box[0][1]), "prob": int(round(score * 100))}`；`prob` 钳位到 [0, 100]。
- `Data.content` = 按 **y 升序、同带（y 差 < 行高一半）内 x 升序**拼块（空格连接，英文块间加一个空格）。
- 返回 `{"Data": json.dumps({"content": ..., "prism_wordsInfo": [...]}, ensure_ascii=False)}`——**Data 必须是 JSON 字符串**（契约 §1.2，上游有二次 json.loads）。
- 引擎懒加载单例 + 锁（照 tts_local 套路）；识别异常向上抛（路由层兜）。
- 在 `capability._MODEL_SPECS` 登记 ocr 条目（包内置模型 → ready 判定 = import 成功）。

## 3. 改造 `backend/app/services/dictation_service.py`（模式路由）

只动 `_get_client()` 一处，**解析/对齐/判分层（_parse_blocks 以下）一行不改**：

```
grade_handwriting 里 raw = _get_client().recognize(image_bytes) 的 _get_client 改为：
  mode = resolve_mode("ocr")
  local ：LocalOcrClient()（失败 → 20005，不碰云端）
  cloud ：现状 _OcrClient()
  hybrid：try LocalOcrClient()（异常或有效块数==0 → log 后走现状 _OcrClient；
          云端也失败 → 20005）
```

- 云端 `_OcrClient`、`_read_ocr_env`、`_get_client` 现有实现与单例语义**原样保留**（cloud 模式仍走它）。
- hybrid 里「有效块数」判定：`_parse_blocks(raw)` 长度 == 0 → 视为本地失败回退云端。判定函数写在 dictation_service 内（能复用 _parse_blocks），不进 ocr_local。
- 日志加 `engine=local/cloud` 字段。

## 4. 手写样张门（01 §7.1）

`backend/scripts/_ocr_local_probe.py`：
- 输入：qa 目录手写样张 png（`qa/*.png` 与 `qa/**/*.png` 搜集；若 <10 张，明确上报「样张不足 N 张，请姚工补拍」，**不准用印刷体图充数**）。
- 每张跑 `LocalOcrClient().recognize` + 现有解析对齐链路（直接 import dictation_service 的函数拼 results），stdout 打印每张的 `word→recognized→correct` 表与汇总行 `通过 x/y`。
- 门：**逐词判分正确率 ≥ 80%** 且无一张「全部误判」。不达标 → 停下上报实测明细，等姚工裁决（可能结论是「OCR 本地化暂缓，保留 hybrid/cloud」）。

## 5. 测试 `backend/app/tests/test_local_ocr.py`（≥7 用例，全部 Mock 引擎）

monkeypatch `LocalOcrClient.recognize`（返回契约 §1.2 同构 map）与 `_OcrClient`：

1. local 模式成功 → 走 LocalOcrClient，results 判分正确（写对/写错/漏写三态各一断言）；
2. local 模式引擎抛异常 → 20005，且云端调用次数=0（**契约红线**）；
3. cloud 模式 → 只走云端（LocalOcrClient 调用次数=0）；
4. hybrid 本地成功 → 云端调用次数=0；
5. hybrid 本地异常 → 回落云端成功；
6. hybrid 本地返回 0 块 → 回落云端成功；
7. 同构适配：LocalOcrClient 返回的 Data 是**字符串**且 json.loads 后含 prism_wordsInfo（直接针对适配器的单测，构造 RapidOCR 原始输出 → 断言 x/y/prob/word 映射）。

## 6. DoD

- [ ] 门脚本达到 §7.1 标准（或按约定上报暂缓）；requirements 版本写死且与批次 B 一致；
- [ ] `pytest app/tests/test_local_ocr.py -q` 全绿；全量 pytest 无新增失败；
- [ ] 现有 OCR 测试（test_s2_dictation.py 等）**零改动**通过——解析层没被动的证明；
- [ ] 上报：RapidOCR 实测单张耗时（ms）、锁定的包版本。

## 7. 禁止事项

- 禁改 `_parse_blocks`/`_align`/`normalize_word`/`grade_handwriting` 的判分逻辑与对外响应结构；
- 禁改 `api/dictation.py`；
- 禁止 paddle 原生框架（只准 onnxruntime 底座）。
