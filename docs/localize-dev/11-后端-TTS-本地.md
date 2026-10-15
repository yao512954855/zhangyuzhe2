# 11 - 批次B · 后端 TTS 本地化（sherpa-onnx + Piper 模型）

> 依赖：批次 A 完成。契约依据：01 §1.1（TTS 行为矩阵）、§3（模型清单）、§6（懒加载）、§7（TTS 门）。

## 1. 依赖安装（版本写死回填 requirements.txt）

```bash
cd backend && env -u HTTP_PROXY -u HTTPS_PROXY .venv/Scripts/pip.exe install sherpa-onnx -i https://pypi.org/simple
pip show sherpa-onnx   # 把实测版本写死进 requirements.txt（新增一行，带注释说明用途）
```

- 若 PyPI 无 Windows wheel 或安装失败 → 试清华镜像 → 仍失败**停下上报**（不准换 torch 系方案）。
- onnxruntime 由 sherpa-onnx 传递引入；**显式钉住** onnxruntime 实测版本（避免与批次 C 的 rapidocr 漂移，两边写同一个版本）。

## 2. 模型下载 `backend/scripts/download_models.py`

- 幂等脚本（文件存在且 >1MB 跳过）；`--only tts` 支持只下 TTS。
- TTS 模型落盘到：
  - `backend/models/tts/vits-piper-en_US-amy-medium/`（.onnx + tokens 等，从 sherpa-onnx releases 的 tar.bz2 解包）
  - `backend/models/tts/vits-melo-tts-zh_en/`（首选中文模型）
- 下载源按 01 §3 顺序（GitHub release 直连 → ghproxy 前缀）；脚本里写死 URL 常量与实际用到的文件清单（以 sherpa-onnx release 页该模型压缩包内实际文件为准，解包后保留 sid/fst/tokens/onnx 全部文件）。
- 下载后跑通一次离线合成再算完成（见 §5 门）。
- `backend/models/` 加进 `.gitignore`（根目录 .gitignore 追加一行 `backend/models/`）。

## 3. 新增 `backend/app/services/tts_local.py`（本地 TTS 引擎）

```python
"""本地 TTS 引擎（sherpa-onnx 离线合成）—— localize-dev/01 §1.1/§6。

懒加载单例 + 双检锁；加载失败置 ready=False（60s 冷却）。
英文 lang=en-US → vits-piper-en_US-amy-medium；中文 zh-CN → vits-melo-tts-zh_en。
输出：16bit PCM → 包成 WAV 字节（RIFF），采样率取模型自身（44100/22050 均可，
客户端 audioplayers 不挑采样率）。
"""

def synthesize_local(text: str, lang: str) -> bytes:   # 失败抛 Exception（由路由层兜）
def is_ready() -> tuple[bool, str]                     # 给 engine_ready 登记用
```

要点：
- 模块级 `_engine: dict[str, object]`（按模型缓存）+ `threading.Lock` 双检锁（契约 §6）。
- 加载失败/模型文件缺失 → 抛异常，**不打印堆栈到 stdout**（走 logger.warning）。
- 合成空文本/纯空白 → 抛异常（上游已有校验，这里兜底）。
- 引擎线程安全性：sherpa offline tts 实例并发合成需串行——用同一把锁包住 synthesize（听写播报是单用户场景，串行可接受，写死这个决策）。
- 在 `app/core/capability.py` 的 `_MODEL_SPECS` 里登记 tts 两模型路径（替换批次 A 占位）。

## 4. 改造 `backend/app/services/tts_service.py`（模式路由）

`synthesize(text, lang)` 改为按契约 §1.1 路由（**对外签名不变**，`api/tts.py` 零改动）：

```
mode = resolve_mode("tts")
cloud 可用性：api_key 存在（现状 _load_api_key 逻辑保留）
local  ：synthesize_local → 成功落缓存(key=engine="local") 返回；失败 → BizError(50006)
hybrid ：try local → 失败 log warning 后走 _synthesize_remote → 失败 BizError(50005)
cloud  ：现状逻辑
缓存键：md5(f"{engine}|{lang}|{text}")   # engine ∈ {"local","cloud"}
```

- `_synthesize_remote`、`_load_api_key`、`_MAX_TEXT_LEN`、参数校验**原样保留**。
- 日志：模式与实际使用引擎打进现有 `TTS 合成 ok` 日志（加 `engine=` 字段）。

## 5. 验收门（01 §7.3）

`backend/scripts/_tts_local_probe.py`：对 `("apple","en-US")("good morning","en-US")("苹果","zh-CN")("你好吗","zh-CN")` 各合成一次，落 `qa/audio/_tts_local_<case>.wav`，stdout 打印 bytes 与 RIFF 头检查结果；4 条全过才算门通过。

## 6. 测试 `backend/app/tests/test_local_tts.py`（≥6 用例，全部 Mock，不起真引擎）

单测通过 monkeypatch `tts_local.synthesize_local` / `tts_service._synthesize_remote` 注入假引擎，模式用 `SettingsService.set` 或 monkeypatch `resolve_mode` 控制：

1. cloud 模式 → 只走 remote（local 函数被调次数=0）；
2. local 模式成功 → 走 local 且不碰 remote；缓存文件落盘键含 "local"；
3. local 模式引擎失败 → 50006，且 remote 调用次数=0（**契约红线：local 不准碰云端**）；
4. hybrid 本地失败 → 自动走 remote 成功；
5. hybrid 两者都失败 → 50005；
6. 缓存命中：同 text+lang+engine 第二次调用引擎函数次数不增加；
7. （接口层）POST /api/tts 在 monkeypatch local 成功时返回 audio/wav 且 Content 头 RIFF。

## 7. DoD

- [ ] 门脚本 4 条全过；requirements 版本写死；`.gitignore` 已加；
- [ ] `pytest app/tests/test_local_tts.py -q` 全绿；全量 pytest 无新增失败；
- [ ] 真机冒烟（可并入批次 F）：`.env` 不锁定 + 管理端切 hybrid → 听写播报首次合成走本地（看日志 `engine=local`）；
- [ ] 上报：模型实测体积、合成耗时（ms/条）、实际锁定的 sherpa-onnx 与 onnxruntime 版本号。

## 8. 禁止事项

- 禁改 `api/tts.py`、student-app 任何文件；
- 禁止 pip 装表外依赖（torch/paddle/edge-tts 等）；
- 禁止把模型文件或下载缓存提交 git。
