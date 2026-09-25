"""ASR 语音网关 —— WP-BE-06（需求 §9，契约 §6 / §7.4 / §9.2）。

| 模块 | 职责 |
|---|---|
| `base` | `ASRTaskConfig` / `ASRProvider`（**契约 §7.4 冻结**）+ 音频规格校验 |
| `mock_provider` | 离线联调（APP 并行开发的前提，也是所有自动化用例的底座） |
| `aliyun_provider` | 百炼：流式（背诵）+ 非流式（说词/拼读）双通道，禁用模型硬拦截 |
| `volcengine_provider` | 火山豆包流式（凭证缺失时明确报错） |
| `factory` | `ASRFactory`（契约签名）+ `ASRRouter` 主备编排 + `load_asr_raw` |
| `hotwords` | 热词组装（**一期恒为空**，保留代码位） |
| `arbitrator` | 多模态仲裁（仅 empty / fuzzy / 申诉 触发，严禁进主链路） |
| `session_manager` | WebSocket 会话与并发控制（全局 60 / 队列 120 / 单账号 1 路） |

`asr_logs` 的写入在 `app/services/asr_log_service.py`（不属于本包，BE-07 会读它）。

**安全红线**：API Key 只在服务端 `asr.yaml`；音频只在内存转发，**不落盘、不入库**（F18）。
"""

from app.services.asr.arbitrator import (
    ARBITRATION_PROMPT,
    TRIGGER_MATCH_TYPES,
    ArbitratorProvider,
)
from app.services.asr.base import (
    ASRError,
    ASRProvider,
    ASRTaskConfig,
    audio_to_data_uri,
    pcm_duration_ms,
    validate_pcm_chunk,
)
from app.services.asr.aliyun_provider import AliyunProvider
from app.services.asr.factory import ASRFactory, ASRRouter, load_asr_raw
from app.services.asr.hotwords import build_hotwords, current_hotwords
from app.services.asr.mock_provider import MockProvider
from app.services.asr.volcengine_provider import VolcengineProvider

__all__ = [
    "ARBITRATION_PROMPT",
    "TRIGGER_MATCH_TYPES",
    "ASRError",
    "ASRFactory",
    "ASRProvider",
    "ASRRouter",
    "ASRTaskConfig",
    "AliyunProvider",
    "ArbitratorProvider",
    "MockProvider",
    "VolcengineProvider",
    "audio_to_data_uri",
    "build_hotwords",
    "current_hotwords",
    "load_asr_raw",
    "pcm_duration_ms",
    "validate_pcm_chunk",
]
