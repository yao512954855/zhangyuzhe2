"""云端语音 API Key 的动态解析（二轮新增：管理端实时更换实时生效）。

**权威源 = settings 表的 `cloud_api_key`**（管理端 PUT /api/settings 实时写）；
回落   = `asr.yaml` 的 `aliyun.api_key`（部署种子，契约 §9.2 安全红线不变：
key 只在服务端，DB 与 asr.yaml 都不出服务）。

为什么每次现取而不是构造时捕获：SettingsService 有进程内缓存
（首次载入后读操作零 SQL），每次解析代价 ≈ 一次 dict 查询；换来的是
「管理端改完即生效」—— 不用重启服务，也不用重建 Provider 实例
（WS 会话进行中重建实例有并发风险）。

覆盖面（百炼 key 同源共用）：
- ASR 流式/非流式：`aliyun_provider.AliyunProvider`
- 仲裁旁路：`arbitrator.ArbitratorProvider`
- 兜底引擎：`fallback.FallbackProvider`
- 云端 TTS：`tts_service._load_api_key`
云端 OCR 将来接入时复用本模块，不再新增凭证文件。
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)

#: settings 表键名（006_apikey.sql 种子）
SETTING_KEY = "cloud_api_key"


def resolve(yaml_key: str = "") -> str:
    """取当前生效的百炼 key：DB settings 非空优先，否则回落 asr.yaml 种子。

    `yaml_key` 是调用方从 `asr.yaml` 读到的种子值（构造时缓存），
    仅在 DB 未配置（空串）时使用。

    ⚠️ 防御：DB 未初始化 / settings 表缺失（测试隔离库、启动早期）时
    **回落种子而不是抛错** —— key 解析在每次 ASR/TTS 请求的热路径上，
    语音能力不能因 DB 状态瘫掉。
    """
    try:
        from app.services.settings_service import SettingsService  # 延迟导入避免循环依赖

        db_key = (SettingsService.get_str(SETTING_KEY, "") or "").strip()
    except Exception as exc:  # noqa: BLE001 —— 刻意兜底，见 docstring
        logger.debug("key_store：settings 读取失败（%s），回落 asr.yaml 种子", exc)
        db_key = ""
    return db_key or (yaml_key or "").strip()


def mask(key: str) -> str:
    """管理端展示用掩码：保留前 4 后 4，中间打码；≤8 位或空则全打码/空串。"""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}****{key[-4:]}"
