"""配置系统 —— 契约《01-共享契约冻结》§9 / 10a F2。

两块独立配置：
- `.env`（服务端环境变量）→ pydantic-settings 加载，**缺失必填项直接启动失败**
- `asr.yaml`（语音厂商配置）→ pyyaml 加载，独立于 .env 便于换厂商不改代码

⚠️ 安全红线（契约 §9.2）：AccessKey 只存在于服务端 `asr.yaml`，
   绝不进入 APK / 前端代码 / 数据库。
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录（config.py 位于 backend/app/core/）
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """`backend/.env` 映射（契约 §9.1）。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ---- 服务 ----
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8810  # 契约 §11 固定 8810

    # ---- 数据库 ----
    DB_PATH: str = "./data/bjsys.db"
    BACKUP_DIR: str = "./backups"

    # ---- 日志 ----
    LOG_LEVEL: str = "INFO"

    # ---- CORS（简化版无鉴权，局域网内任意设备可访问，单家庭场景接受）----
    CORS_ORIGINS: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def db_file(self) -> Path:
        """数据库文件绝对路径（`DB_PATH` 相对 backend/ 解析）。"""
        p = Path(self.DB_PATH)
        return p if p.is_absolute() else (BASE_DIR / p).resolve()

    @property
    def backup_dir(self) -> Path:
        p = Path(self.BACKUP_DIR)
        return p if p.is_absolute() else (BASE_DIR / p).resolve()

    @property
    def log_dir(self) -> Path:
        return BASE_DIR / "logs"

    @property
    def migrations_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "migrations"


# ---------------------------------------------------------------------------
# asr.yaml 结构（契约 §9.2）
# ---------------------------------------------------------------------------

class AliyunASRConfig(BaseModel):
    api_key: str = ""
    streaming_model: str = ""   # 语文背诵（流式）
    batch_model: str = ""       # 英语说词/拼读（非流式）
    verify_model: str = ""      # 拼读交叉校验（可选）
    ws_endpoint: str = ""
    http_endpoint: str = ""
    http_format: str = "wav"    # HTTP 调用必须显式传 parameters.format


class VolcengineASRConfig(BaseModel):
    app_id: str = ""
    access_token: str = ""
    model: str = "doubao-streaming-asr"
    endpoint: str = ""


class HotwordsConfig(BaseModel):
    enabled: bool = False
    sources: list[str] = Field(default_factory=list)


class ArbitratorConfig(BaseModel):
    enabled: bool = False
    provider: str = ""
    model: str = ""
    api_key: str = ""
    trigger: list[str] = Field(default_factory=lambda: ["empty", "fuzzy", "appeal"])


class ASRLimits(BaseModel):
    connect_timeout_ms: int = 3000
    chunk_ms: int = 150
    max_session_seconds: int = 300


class ASRConfig(BaseModel):
    """`asr.yaml` 映射。文件缺失时全部取默认值（ASR 未就绪）。"""

    provider: str = "aliyun"
    backup_provider: str = ""
    aliyun: AliyunASRConfig = Field(default_factory=AliyunASRConfig)
    volcengine: VolcengineASRConfig = Field(default_factory=VolcengineASRConfig)
    lang_map: dict[str, str] = Field(
        default_factory=lambda: {
            "word": "en",
            "spell": "zh",      # ⚠️ 拼读用 zh，不是 en（契约 §6.3）
            "zh_answer": "zh",
            "recite": "zh",
        }
    )
    hotwords: HotwordsConfig = Field(default_factory=HotwordsConfig)
    arbitrator: ArbitratorConfig = Field(default_factory=ArbitratorConfig)
    limits: ASRLimits = Field(default_factory=ASRLimits)

    @property
    def ready(self) -> bool:
        """ASR 是否就绪 —— 健康检查（F19）据此返回 asr_ready。

        判定：主厂商存在且其凭证非空即可。一期主厂商是 aliyun。
        """
        if self.provider == "aliyun":
            return bool(self.aliyun.api_key)
        if self.provider == "volcengine":
            return bool(self.volcengine.app_id and self.volcengine.access_token)
        return False


# ---------------------------------------------------------------------------
# 加载入口
# ---------------------------------------------------------------------------

def _fail_fast(msg: str) -> None:
    """配置缺失时给出可操作的提示并退出（不静默用默认值）。"""
    sys.stderr.write(f"\n[配置错误] {msg}\n\n")
    sys.exit(1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """加载并缓存 .env 配置。缺失必填项时直接退出进程。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        _fail_fast(
            f"未找到配置文件 {env_file}\n"
            f"  请先复制模板：copy .env.example .env\n"
            f"  然后重新启动。"
        )
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:  # pydantic 校验失败
        _fail_fast(f"配置文件校验未通过：{exc}")


@lru_cache(maxsize=1)
def get_asr_config() -> ASRConfig:
    """加载 `asr.yaml`。

    文件缺失不算致命（语音是 BE-06 的职责，服务本身应能起来），
    此时返回默认配置、`ready=False`，健康检查里会如实反映。
    """
    yaml_path = BASE_DIR / "asr.yaml"
    if not yaml_path.exists():
        return ASRConfig()
    try:
        raw: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception:  # pragma: no cover - YAML 语法错误
        return ASRConfig()
    return ASRConfig(**raw)


def reset_config_cache() -> None:
    """测试用：清空缓存以便重新读取配置。"""
    get_settings.cache_clear()
    get_asr_config.cache_clear()
