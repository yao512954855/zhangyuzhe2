"""多模态仲裁 —— WP-BE-06 F19（需求 §9.7，一期启用）。

## 为什么要有它

预研实测（`qa/ASR预研报告.md` §4.4）：专用 ASR 与多模态模型在拼读样本上**互补而非包含** ——
ASR 在 `pig` 上更准，omni 在 `water` / `pencil` / `restaurant` 上更准（22 条里有 4 条判断不一致）。
所以「ASR 判不准时再看一眼」是真的能救回分数的。

## 硬边界（写死在代码里，不靠自觉）

1. **触发场景严格三条**：ASR 空结果 / 判定 `fuzzy` / 学生申诉。**严禁进主链路。**
2. `asr.yaml` 的 `arbitrator.enabled = false` 时，行为与「没有仲裁」完全一致（直接降级）。
3. 仲裁**仍失败**才降级键盘（契约 §6.5）。

## 两种仲裁后端

| `arbitrator.model` | 说明 |
|---|---|
| `qwen3.5-omni-plus`（默认） | 多模态：音频 + 固定提示词 → 字母序列。更准，但延迟 600–1300ms、按 token 计费 |
| `qwen-audio-3.0-asr-flash` | 第二个 ASR：延迟与成本都更低，与 `fun-asr-flash` 存在互补 |

**成本与延迟**决定了它只能出现在上面那三个场景里 —— 主链路每题都走仲裁，
语文背诵会被秒级延迟毁掉，费用也会翻数倍。
"""

from __future__ import annotations

from typing import Any, Literal

from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.services.asr.base import ASRError, audio_to_data_uri
from app.services.asr.fallback import FallbackProvider

logger = get_logger(__name__)

#: 多模态仲裁的固定提示词（F19 原文，不要改写 —— 预研就是用它测出的互补性结论）
ARBITRATION_PROMPT = "这段音频里，说话人依次念出了哪些英文字母？只输出字母序列，用空格分隔"

#: 允许触发仲裁的判定结果（F19：严格三条，其余一律不触发）
TRIGGER_MATCH_TYPES: frozenset[str] = frozenset({"empty", "fuzzy"})

#: `arbitrator.trigger` 配置值 → 内部场景名
_SCENARIOS: dict[str, str] = {
    "empty": "empty",
    "fuzzy": "fuzzy",
    "appeal": "appeal",
}


class ArbitratorProvider:
    """仲裁器：给定音频，返回「第二个判据」的识别文本。"""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        section = dict((config or {}).get("arbitrator") or {})
        self.enabled: bool = bool(section.get("enabled"))
        self.provider: str = str(section.get("provider") or "aliyun")
        self.model: str = str(section.get("model") or "qwen3.5-omni-plus")
        self.api_key: str = str(
            section.get("api_key")
            or ((config or {}).get("aliyun") or {}).get("api_key")
            or ""
        )
        self.trigger: set[str] = {
            s for s in (section.get("trigger") or ["empty", "fuzzy", "appeal"])
            if s in _SCENARIOS
        }
        aliyun = (config or {}).get("aliyun") or {}
        self.endpoint: str = str(section.get("endpoint") or aliyun.get("http_endpoint") or "")
        self.http_format: str = str(aliyun.get("http_format") or "wav")
        self.timeout_s: float = float(section.get("timeout_ms") or 5000) / 1000.0
        # WP-2C：接入兜底引擎。仲裁仍只在 empty/fuzzy/appeal 场景把门（见 should_trigger），
        # 但「自身后端无结果」时，把同一段音频交给兜底引擎再识别一次（仍是旁路，未进主链路）。
        self.fallback: FallbackProvider = FallbackProvider(config)

    # ------------------------------------------------------------------ 判定

    @property
    def kind(self) -> Literal["multimodal", "asr"]:
        """仲裁后端类型：多模态 or 第二个 ASR（只看模型名，配置驱动）。"""
        return "asr" if "fun-asr" in self.model or "asr-flash" in self.model else "multimodal"

    def should_trigger(self, match_type: str | None = None, *, appeal: bool = False) -> bool:
        """是否应触发仲裁。**这是防止仲裁进主链路的唯一入口。**"""
        if not self.enabled or not self.api_key or not self.endpoint:
            return False
        if appeal:
            return "appeal" in self.trigger
        if match_type is None:
            return False
        if match_type == "empty":
            return "empty" in self.trigger
        if match_type == "fuzzy":
            return "fuzzy" in self.trigger
        return False

    # ------------------------------------------------------------------ 调用

    async def arbitrate(self, pcm: bytes) -> str:
        """拿第二判据的识别文本（仲裁自身后端 → 兜底引擎，两层都失败才返回空）。

        Args:
            pcm: 本次任务的音频（**内存里的那一份**，不落盘）。

        Returns:
            识别文本；两层都失败返回空串（**不抛异常** —— 仲裁/兜底失败不该把主流程带崩，
            由调用方按「仍失败 → 降级键盘」处理）。
        """
        if not pcm:
            return ""
        # 1) 一期逻辑：仲裁器自身后端（empty/fuzzy/appeal 场景的第二判据）
        text = await self._arbitrate_via_self(pcm)
        if text:
            return text
        # 2) WP-2C：接入兜底引擎。仅当仲裁自身无结果时再试一次；
        #    仍是 empty/fuzzy/appeal 场景内（should_trigger 已经把门），未进主链路。
        #    兜底引擎同样拒绝接收 expected（红线），这里恒定传 None。
        if self.fallback.enabled:
            fb = await self.fallback.fallback(pcm, expected=None)
            if fb:
                return fb
        return ""

    async def _arbitrate_via_self(self, pcm: bytes) -> str:
        """仲裁器自身后端的识别（原 `arbitrate` 逻辑，单独抽出便于兜底降级）。"""
        payload = self._build_payload(pcm)
        try:
            body = await self._call(payload, self._headers())
        except Exception as exc:  # noqa: BLE001 - 仲裁是旁路，失败只记日志
            logger.warning("仲裁调用失败（按未通过处理）：%s", exc)
            return ""
        return self._parse(body)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, pcm: bytes) -> dict[str, Any]:
        audio = audio_to_data_uri(pcm, self.http_format)
        if self.kind == "asr":
            return {
                "model": self.model,
                "input": {"messages": [{"role": "user", "content": [{"audio": audio}]}]},
                "parameters": {"format": self.http_format, "sample_rate": 16000},
            }
        return {
            "model": self.model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"audio": audio}, {"text": ARBITRATION_PROMPT}],
                    }
                ]
            },
            # 固定提示词 + 只输出字母序列 → 不需要采样，降低费用与波动
            "parameters": {"temperature": 0.0, "result_format": "message"},
        }

    async def _call(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """真正的 HTTP 调用。**测试与烟测通过 monkeypatch 本方法注入假响应。**"""
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False) as client:
            resp = await client.post(self.endpoint, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE,
                f"仲裁接口返回 {resp.status_code}",
                status="error",
            )
        return resp.json()

    @staticmethod
    def _parse(body: dict[str, Any]) -> str:
        """从多模态/ASR 响应里取文本。"""
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
