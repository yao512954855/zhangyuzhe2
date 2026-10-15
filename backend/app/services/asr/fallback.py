"""多模态兜底判定 —— WP-2C（需求 §9.7 / §12.2，二期）。

## 它和一期仲裁（arbitrator）的关系

一期 `ArbitratorProvider` 是「**判分结果级**」的第二判据：只在
`empty` / `fuzzy` / `申诉` 三种**判分结果**场景触发（见 `arbitrator.py`）。

本模块 `FallbackProvider` 是「**ASR 链路级**」的兜底：当**专用 ASR 本身不行**时，
在降级键盘之前，用多模态模型对这段音频再做一次兜底识别或对结果做复核：

| 触发 | 含义 | 对应契约 |
|---|---|---|
| `asr_failed=True` | ASR 抛错 / 超时（整段没识别出来） | §9.4 降级策略：超时/失败先别急着键盘 |
| `confidence < 阈值` | ASR 给了结果但置信度低（疑似听错） | §9.7「不要把孩子赶去键盘」 |

**严禁进主链路**：`should_trigger` 只在「失败 / 低置信」时返回 True；
一次**干净的高置信结果**永远不触发兜底（否则每题都走多模态，延迟秒级、按 token 计费，
语文逐字点亮会被毁掉）。这与仲裁的「只三条、不进主链路」是同一根红线。

## 硬红线（本模块是唯一的强制点，任何调用方都绕不过）

1. ⛔ **禁用 `qwen3-asr-flash`**（及其 `-realtime`）：它会把学生错读**脑补**成正确词
   （`GLASS→CLASS`、`ABPLE→APPLE`），用于判分等于放过错读，属教学事故
   （需求 §9.7 / 选型报告 §1.2）。配置里写了就**直接抛错**，绝不静默使用。
2. ⛔ **`expected` 标准答案绝不发给云厂商**：`fallback()` 收到非 `None` 的 `expected`
   直接拒绝；请求体里也只包含「音频 + 固定提示词」，绝不含答案文本（契约 §6.3）。
3. 音频只在内存里，不落盘（F18）。
"""

from __future__ import annotations

from typing import Any, Literal

from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.services.asr.base import ASRError, audio_to_data_uri

logger = get_logger(__name__)

#: ⛔ 禁用清单：会把错读脑补成正确词的模型，绝不允许用于兜底/判分（需求 §9.7）
BLOCKED_MODELS: frozenset[str] = frozenset(
    {"qwen3-asr-flash", "qwen3-asr-flash-realtime"}
)

#: 兜底识别的固定提示词 —— **忠实转录、不做纠正**（与仲裁互补，且绝不能脑补答案）。
#: 注意：提示词里**不含也不引用**任何答案文本，避免误把 expected 带进请求体。
FALLBACK_PROMPT = (
    "请尽量忠实地转录这段音频中说话人实际说出的内容，"
    "不要做任何纠正、补全或猜测；只输出你真实听到的原文。"
)

#: 默认低置信阈值（配置可改，不硬编码在逻辑里）。
DEFAULT_CONFIDENCE_THRESHOLD = 0.5


class FallbackProvider:
    """多模态兜底识别引擎：ASR 失败 / 低置信时的最后一次识别机会。

    配置来自 `asr.yaml` 的 `fallback:` 段（见 `asr.yaml.example`）。
    所有开关、模型、阈值、提示词都走配置；代码里只有默认值。
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw = dict(config or {})
        section = dict(raw.get("fallback") or {})

        self.enabled: bool = bool(section.get("enabled"))
        self.provider: str = str(section.get("provider") or "aliyun")
        self.model: str = str(
            section.get("model") or "qwen3.5-omni-plus"
        )
        aliyun = raw.get("aliyun") or {}
        self.api_key: str = str(
            section.get("api_key") or aliyun.get("api_key") or ""
        )
        self.endpoint: str = str(
            section.get("endpoint") or aliyun.get("http_endpoint") or ""
        )
        self.http_format: str = str(aliyun.get("http_format") or "wav")
        self.confidence_threshold: float = float(
            section.get("confidence_threshold") or DEFAULT_CONFIDENCE_THRESHOLD
        )
        # 两个触发开关（默认都开，可单独关）
        self.trigger_on_error: bool = bool(section.get("trigger_on_error", True))
        self.trigger_on_low_confidence: bool = bool(
            section.get("trigger_on_low_confidence", True)
        )
        self.timeout_s: float = float(section.get("timeout_ms") or 5000) / 1000.0
        self.prompt: str = str(section.get("prompt") or FALLBACK_PROMPT)

        # ⛔ 红线 1：禁用模型硬拦截（配置即错，不静默降级到别的模型）
        if self.enabled and self.model in BLOCKED_MODELS:
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE,
                f"fallback.model={self.model!r} 在禁用清单中，"
                f"禁止用于兜底判定（会把错读脑补成正确词，属教学事故）",
                status="error",
            )

    # ------------------------------------------------------------------ 门控

    def should_trigger(
        self,
        *,
        asr_failed: bool = False,
        confidence: float | None = None,
    ) -> bool:
        """是否应触发兜底。**这是防止兜底进主链路的唯一入口。**

        Args:
            asr_failed: 专用 ASR 本次是否抛错 / 超时（整段没出结果）。
            confidence: ASR 给出的结果置信度（无则传 None）。

        Returns:
            仅在「ASR 失败」或「置信度低于阈值」时 True；
            一次干净的高置信结果 → 永远 False。
        """
        # 没启用 / 没凭证 / 没端点 → 不可用，绝不触发
        if not self.enabled or not self.api_key or not self.endpoint:
            return False
        if asr_failed and self.trigger_on_error:
            return True
        if (
            self.trigger_on_low_confidence
            and confidence is not None
            and confidence < self.confidence_threshold
        ):
            return True
        return False

    # ------------------------------------------------------------------ 调用

    async def fallback(self, pcm: bytes, *, expected: str | None = None) -> str | None:
        """对一段音频做兜底识别。

        Args:
            pcm: 本次任务的音频（内存里的那一份，不落盘）。
            expected: **必须传 None**。任何把标准答案下发给云厂商的企图都会被拒绝。

        Returns:
            兜底识别文本；失败 / 无结果返回 `None`（调用方据此「仲裁仍失败 → 降级键盘」）。
        """
        # ⛔ 红线 2：绝不把 expected 标准答案下发给云厂商（契约 §6.3）
        if expected is not None:
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE,
                "兜底判定拒绝把 expected 标准答案下发给云厂商（契约 §6.3 红线）",
                status="error",
            )
        if not pcm:
            return None
        payload = self._build_payload(pcm)
        try:
            body = await self._call(payload, self._headers())
        except Exception as exc:  # noqa: BLE001 - 兜底是旁路，失败只记日志，由调用方降级
            logger.warning("兜底识别失败（按未通过处理，降级键盘）：%s", exc)
            return None
        return self._parse(body) or None

    # ------------------------------------------------------------------ 内部

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, pcm: bytes) -> dict[str, Any]:
        """请求体：**只有音频 + 固定提示词**，绝不含 expected。"""
        audio = audio_to_data_uri(pcm, self.http_format)
        return {
            "model": self.model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"audio": audio},
                            {"text": self.prompt},
                        ],
                    }
                ]
            },
            # 固定提示词 + 只输出原文 → 不需要采样，降低费用与波动
            "parameters": {"temperature": 0.0, "result_format": "message"},
        }

    async def _call(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """真正的 HTTP 调用。**测试通过 monkeypatch 本方法注入假响应。**"""
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False) as client:
            resp = await client.post(self.endpoint, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE,
                f"兜底接口返回 {resp.status_code}",
                status="error",
            )
        return resp.json()

    @staticmethod
    def _parse(body: dict[str, Any]) -> str:
        """从多模态响应里取文本（与仲裁共用解析思路）。"""
        out = body.get("output") or {}
        inner = out.get("output") if isinstance(out.get("output"), dict) else out
        if isinstance(inner, dict):
            sentence = inner.get("sentence")
            if isinstance(sentence, dict) and sentence.get("text") is not None:
                return str(sentence["text"]).strip()
            choices = inner.get("choices")
            if isinstance(choices, list) and choices:
                msg = (choices[0] or {}).get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict) and first.get("text") is not None:
                        return str(first["text"]).strip()
            if inner.get("text") is not None:
                return str(inner["text"]).strip()
        return ""


__all__ = ["BLOCKED_MODELS", "FallbackProvider", "FALLBACK_PROMPT"]
