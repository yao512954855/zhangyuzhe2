"""ASR 工厂与主备编排 —— WP-BE-06 F5（契约 §7.4）。

```python
ASRFactory.create("aliyun", config_dict)     # 契约签名：单个实例
ASRRouter(config_dict)                       # 本包补充：主备编排
```

`ASRFactory.create` 的签名与契约逐字一致；`ASRRouter` 是在它之上的**主备编排**，
因为契约只说了「主厂商失败 → 自动创建备用厂商实例」，没说「谁来记住这个切换」——
放在工厂里最自然，且 WebSocket 会话层只需面对一个对象。

主备规则（F5 / U9）：

- `backup_provider` 为空 → 不启用主备，直接返回主厂商。
- 主厂商**建连失败** → 立刻改用备厂商（本次任务）。
- 主厂商**连续 3 次错误** → 之后**一直**用备厂商（粘性切换）。
- `asr_logs.provider` 必须记录**实际用上的**那个厂商（`ASRRouter.used_provider`）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from app.core.config import BASE_DIR
from app.core.logging import get_logger
from app.services.asr.aliyun_provider import AliyunProvider
from app.services.asr.base import ASRError, ASRProvider, ASRTaskConfig
from app.services.asr.local_provider import LocalProvider
from app.services.asr.mock_provider import MockProvider
from app.services.asr.volcengine_provider import VolcengineProvider

logger = get_logger(__name__)

#: 支持的厂商 → Provider 类（local：契约修订 v1.0.2，2026-10-13 姚工裁决接线）
PROVIDERS: dict[str, type[ASRProvider]] = {
    "aliyun": AliyunProvider,
    "volcengine": VolcengineProvider,
    "mock": MockProvider,
    "local": LocalProvider,
}

#: 连续失败多少次后粘性切换到备厂商（F5）
FAILOVER_THRESHOLD = 3


def load_asr_raw(path: Path | None = None) -> dict[str, Any]:
    """读 `asr.yaml` 的**原始 dict**。

    为什么不用 `app.core.config.get_asr_config()`：那里返回的是 `ASRConfig` 模型，
    `extra` 会被 pydantic 丢掉 —— 而 `mock` / `limits` 这类**运行时编排参数**不在模型里，
    工厂与会话层需要它们。文件缺失返回 `{}`（语音未配置时服务仍应能起来）。
    """
    env_path = os.environ.get("ASR_CONFIG_PATH")
    if path is not None:
        yaml_path = Path(path)
    elif env_path:
        yaml_path = Path(env_path)
    else:
        yaml_path = BASE_DIR / "asr.yaml"
    if not Path(yaml_path).exists():
        return {}
    try:
        return yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - YAML 语法错误
        logger.error("asr.yaml 解析失败：%s", exc)
        return {}


class ASRFactory:
    """按配置创建 Provider 实例（契约 §7.4 签名）。"""

    @staticmethod
    def create(provider: str, config: dict[str, Any] | None = None) -> ASRProvider:
        """创建 Provider。

        Args:
            provider: `'aliyun'` | `'volcengine'` | `'mock'`。
            config: `asr.yaml` 的原始 dict（各 Provider 各自取自己那一段）。

        Raises:
            ASRError: 未知厂商名（配置写错要立刻报，不能静默回退到某一个）。
        """
        key = str(provider or "").strip().lower()
        cls = PROVIDERS.get(key)
        if cls is None:
            raise ASRError(
                50001,
                f"未知的 ASR provider：{provider!r}（可选：{', '.join(sorted(PROVIDERS))}）",
                status="error",
            )
        return cls(config or {})


class ASRRouter:
    """主备编排：一次识别任务的 Provider 选择、失败计数与粘性切换。"""

    def __init__(
        self,
        raw_config: dict[str, Any] | None = None,
        *,
        failover_threshold: int = FAILOVER_THRESHOLD,
    ) -> None:
        self.raw = dict(raw_config or {})
        self.primary_name = str(self.raw.get("provider") or "aliyun").strip().lower()
        self.backup_name = str(self.raw.get("backup_provider") or "").strip().lower()
        self.failover_threshold = int(
            (self.raw.get("limits") or {}).get("failover_threshold") or failover_threshold
        )
        self._consecutive_failures = 0
        self._switched = False
        self.used_provider: str = self.primary_name

    # ------------------------------------------------------------------ 属性

    @property
    def has_backup(self) -> bool:
        return bool(self.backup_name)

    @property
    def switched(self) -> bool:
        """是否已粘性切到备厂商。"""
        return self._switched

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    # ------------------------------------------------------------------ 编排

    def build(self, name: str) -> ASRProvider:
        return ASRFactory.create(name, self.raw)

    async def start(self, cfg: ASRTaskConfig) -> ASRProvider:
        """启动任务：主厂商建连失败则改用备厂商。

        Returns:
            真正完成 `start_session` 的 Provider（`used_provider` 已更新）。
        """
        order: list[str]
        if self._switched and self.has_backup:
            order = [self.backup_name]
        elif self.has_backup:
            order = [self.primary_name, self.backup_name]
        else:
            order = [self.primary_name]

        last_exc: Exception | None = None
        for i, name in enumerate(order):
            try:
                # ⚠️ `build` 必须也在 try 里：厂商名写错时 `ASRFactory.create` 会抛，
                #    那不是「建连失败」而是「配置错误」，但**同样应该尝试备用厂商** ——
                #    否则主厂商名字打错一个字母就把语音整条链路打死。
                provider = self.build(name)
                await provider.start_session(cfg)
            except Exception as exc:  # noqa: BLE001 - 建连失败沿用下一个
                last_exc = exc
                logger.warning(
                    "ASR 厂商 %s 建连失败（第 %d 顺位）：%s", name, i + 1, exc
                )
                self._on_failure(permanent=False)
                continue
            self.used_provider = name
            if i > 0:      # 用上了备厂商 → 主厂商视为不可用
                self._switched = True
                logger.warning("ASR 已切到备厂商：%s", name)
            return provider

        raise last_exc if isinstance(last_exc, Exception) else ASRError(
            50001, "所有 ASR 厂商均不可用", status="error"
        )

    def note_ok(self) -> None:
        """一次识别成功 —— 连续失败计数清零。"""
        self._consecutive_failures = 0

    def note_failure(self) -> None:
        """一次识别失败（非建连）—— 达到阈值则粘性切备厂商。"""
        self._on_failure(permanent=True)

    # ------------------------------------------------------------------ 内部

    def _on_failure(self, *, permanent: bool) -> None:
        self._consecutive_failures += 1
        if (
            permanent
            and self.has_backup
            and not self._switched
            and self._consecutive_failures >= self.failover_threshold
        ):
            self._switched = True
            logger.warning(
                "ASR 主厂商连续失败 %d 次，粘性切换到备厂商 %s",
                self._consecutive_failures,
                self.backup_name,
            )
