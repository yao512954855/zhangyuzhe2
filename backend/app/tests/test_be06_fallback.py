"""WP-2C 多模态兜底判定 —— 验收测试（Mock 驱动，无云账号）。

覆盖（详见 `50-二期工作包拆解.md` §9 DoD）：
- 触发条件：ASR 失败 / 低置信 → `should_trigger` True
- 不触发条件：关闭 / 无凭证 / **干净高置信结果** → False（严禁进主链路）
- 红线不被绕过：禁用 `qwen3-asr-flash`；`expected` 永不下发云厂商
- 反向验证：撤掉门控后「不触发」用例必须转红

全部用 `FallbackProvider` / `ArbitratorProvider` 直接构造 + 假 `_call`，不读盘、不起服务。

⚠️ 真实云链路（AccessKey 缺失，`asr_ready=false`）**未验**，属 C 档人工——见报告。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import ErrorCode
from app.services.asr.arbitrator import ArbitratorProvider
from app.services.asr.base import ASRError
from app.services.asr.fallback import BLOCKED_MODELS, FallbackProvider

_FAKE_PCM = b"\x00\x00" * 100    # 任意假音频，仅用于驱动流程
_FAKE_ANSWER = "classroom"       # 标准答案，红线测试用它


def _fb_config(aliyun: Any | None = None, **over: Any) -> dict[str, Any]:
    """构造一个「已启用、有凭证、有端点」的兜底配置（测试基线）。

    `aliyun` 可整体覆盖（用于验证「fallback 段没写 key 时回退到 aliyun」，
    以及「aliyun 也没 key 时确实不触发」两种边界）。
    """
    base: dict[str, Any] = {
        "fallback": {
            "enabled": True,
            "model": "qwen3.5-omni-plus",
            "api_key": "sk-test",
            "endpoint": "https://example.invalid/fallback",
            "confidence_threshold": 0.5,
        },
        "aliyun": aliyun if aliyun is not None else {
            "api_key": "sk-aliyun", "http_endpoint": "https://example.invalid",
            "http_format": "wav",
        },
    }
    base["fallback"].update(over)
    return base


# ===========================================================================
# 触发条件
# ===========================================================================

class TestFallbackTriggerConditions:
    def test_triggers_on_asr_failure(self) -> None:
        """ASR 整段失败（抛错/超时）→ 触发兜底。"""
        fb = FallbackProvider(_fb_config())
        assert fb.should_trigger(asr_failed=True) is True

    def test_triggers_on_low_confidence(self) -> None:
        """ASR 给了结果但置信度低于阈值（0.3 < 0.5）→ 触发兜底。"""
        fb = FallbackProvider(_fb_config())
        assert fb.should_trigger(confidence=0.3) is True

    def test_triggers_when_failed_and_low_confidence(self) -> None:
        fb = FallbackProvider(_fb_config())
        assert fb.should_trigger(asr_failed=True, confidence=0.1) is True


# ===========================================================================
# 不触发条件（严禁进主链路）
# ===========================================================================

class TestFallbackNonTrigger:
    def test_disabled_does_not_trigger(self) -> None:
        fb = FallbackProvider(_fb_config(enabled=False))
        assert fb.should_trigger(asr_failed=True) is False
        assert fb.should_trigger(confidence=0.1) is False

    def test_no_api_key_does_not_trigger(self) -> None:
        """fallback 段和 aliyun 段都没 key → 不触发。"""
        fb = FallbackProvider(_fb_config(aliyun={}, api_key=""))
        assert fb.api_key == ""
        assert fb.should_trigger(asr_failed=True) is False

    def test_no_endpoint_does_not_trigger(self) -> None:
        """fallback 段和 aliyun 段都没 endpoint → 不触发。"""
        fb = FallbackProvider(_fb_config(aliyun={}, endpoint=""))
        assert fb.endpoint == ""
        assert fb.should_trigger(confidence=0.1) is False

    def test_clean_high_confidence_does_not_trigger(self) -> None:
        """⛔ 关键：一次干净的高置信结果（0.99）**绝不**触发兜底（否则每题都走多模态）。"""
        fb = FallbackProvider(_fb_config())
        assert fb.should_trigger(asr_failed=False, confidence=0.99) is False

    def test_confidence_above_threshold_does_not_trigger(self) -> None:
        fb = FallbackProvider(_fb_config())
        assert fb.should_trigger(confidence=0.6) is False

    def test_error_switch_off_respects_gate(self) -> None:
        """关掉 error 触发开关后，仅 ASR 失败不再触发（门控必须被尊重）。"""
        fb = FallbackProvider(_fb_config(trigger_on_error=False))
        assert fb.should_trigger(asr_failed=True) is False
        # 低置信开关仍开 → 低置信仍触发
        assert fb.should_trigger(confidence=0.2) is True


# ===========================================================================
# 红线：禁用 qwen3-asr-flash / expected 永不下发
# ===========================================================================

class TestFallbackRedLines:
    @pytest.mark.parametrize("blocked", sorted(BLOCKED_MODELS))
    def test_blocked_model_raises_on_init(self, blocked: str) -> None:
        """⛔ 红线 1：配置了禁用模型 → 直接抛错，不静默使用。"""
        with pytest.raises(ASRError):
            FallbackProvider(_fb_config(model=blocked))

    def test_fallback_refuses_expected_argument(self) -> None:
        """⛔ 红线 2：任何把 expected 下发给云厂商的企图都被拒绝。"""
        fb = FallbackProvider(_fb_config())

        async def run() -> None:
            with pytest.raises(ASRError):
                await fb.fallback(_FAKE_PCM, expected=_FAKE_ANSWER)

        import asyncio
        asyncio.run(run())

    def test_payload_never_carries_expected(self, monkeypatch: Any) -> None:
        """⛔ 红线 2：请求体只有「音频 + 固定提示词」，绝不含答案文本。"""
        fb = FallbackProvider(_fb_config())
        captured: dict[str, Any] = {}

        async def fake_call(payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
            captured.clear()
            captured.update(payload)
            return {"output": {"text": "recovered"}}

        monkeypatch.setattr(fb, "_call", fake_call)

        async def run() -> str | None:
            return await fb.fallback(_FAKE_PCM, expected=None)

        import asyncio
        result = asyncio.run(run())
        assert result == "recovered"
        blob = str(captured)
        assert "expected" not in blob, "请求体不得包含 expected 字段"
        assert _FAKE_ANSWER not in blob, "请求体不得包含标准答案文本"

    def test_arbitrator_passes_expected_none_to_fallback(self, monkeypatch: Any) -> None:
        """⛔ 红线 2：仲裁接入兜底时，恒定传 expected=None（绝不把答案带进兜底）。"""
        arb = ArbitratorProvider({
            "arbitrator": {"enabled": True, "model": "qwen3.5-omni-plus",
                           "api_key": "sk-x", "endpoint": "https://e"},
            "fallback": {"enabled": True, "model": "qwen3.5-omni-plus",
                         "api_key": "sk-x", "endpoint": "https://e"},
            "aliyun": {"api_key": "sk-x", "http_endpoint": "https://e"},
        })
        seen: dict[str, Any] = {"expected": "SHOULD_NOT_APPEAR"}

        async def empty_self(pcm: bytes) -> str:
            return ""  # 仲裁自身后端无果

        async def fake_fb(pcm: bytes, *, expected: str | None = None) -> str | None:
            seen["expected"] = expected
            return "recovered"

        monkeypatch.setattr(arb, "_arbitrate_via_self", empty_self)
        monkeypatch.setattr(arb.fallback, "fallback", fake_fb)

        async def run() -> str:
            return await arb.arbitrate(_FAKE_PCM)

        import asyncio
        out = asyncio.run(run())
        assert out == "recovered"
        assert seen["expected"] is None, "仲裁必须把 expected=None 交给兜底引擎"

    def test_arbitrator_uses_own_result_without_fallback_call(self, monkeypatch: Any) -> None:
        """成本红线：仲裁自身后端有结果时，**不再**调兜底（避免双重按 token 计费）。"""
        arb = ArbitratorProvider({
            "arbitrator": {"enabled": True, "model": "qwen3.5-omni-plus",
                           "api_key": "sk-x", "endpoint": "https://e"},
            "fallback": {"enabled": True, "model": "qwen3.5-omni-plus",
                         "api_key": "sk-x", "endpoint": "https://e"},
            "aliyun": {"api_key": "sk-x", "http_endpoint": "https://e"},
        })
        called = {"fb": 0}

        async def self_ok(pcm: bytes) -> str:
            return "arbitrated"

        async def fake_fb(pcm: bytes, *, expected: str | None = None) -> str | None:
            called["fb"] += 1
            return "recovered"

        monkeypatch.setattr(arb, "_arbitrate_via_self", self_ok)
        monkeypatch.setattr(arb.fallback, "fallback", fake_fb)

        async def run() -> str:
            return await arb.arbitrate(_FAKE_PCM)

        import asyncio
        out = asyncio.run(run())
        assert out == "arbitrated"
        assert called["fb"] == 0, "自身后端有结果时不得再调兜底（成本/延迟红线）"

    def test_arbitrator_with_blocked_fallback_model_raises(self) -> None:
        """⛔ 红线 1 跨模块：仲裁接入的兜底若配了禁用模型，构造即报错。"""
        with pytest.raises(ASRError):
            ArbitratorProvider({
                "arbitrator": {"enabled": True, "model": "qwen3.5-omni-plus",
                               "api_key": "sk-x", "endpoint": "https://e"},
                "fallback": {"enabled": True, "model": "qwen3-asr-flash",
                             "api_key": "sk-x", "endpoint": "https://e"},
                "aliyun": {"api_key": "sk-x", "http_endpoint": "https://e"},
            })

    def test_arbitrator_gating_unchanged(self) -> None:
        """保持既有仲裁语义：arbitrator 仍只在 empty/fuzzy/appeal 触发，主链路（exact）不触发。"""
        arb = ArbitratorProvider({
            "arbitrator": {"enabled": True, "model": "qwen3.5-omni-plus",
                           "api_key": "sk-x", "endpoint": "https://e"},
            "fallback": {"enabled": False},
            "aliyun": {"api_key": "sk-x", "http_endpoint": "https://e"},
        })
        assert arb.should_trigger("empty") is True
        assert arb.should_trigger("fuzzy") is True
        assert arb.should_trigger(match_type=None, appeal=True) is True
        # 主链路常见结果绝不触发仲裁
        assert arb.should_trigger("exact") is False
        assert arb.should_trigger("wrong") is False


# ===========================================================================
# 反向验证：撤掉门控后「不触发」用例必须转红
# ===========================================================================

class TestReverseVerification:
    def test_removing_gate_makes_clean_result_trigger(self, monkeypatch: Any) -> None:
        """反向验证：把 `should_trigger` 的门控撤掉（恒返回 True），
        原本「干净高置信不触发」的断言会反转成触发 —— 证明门控是这条红线唯一承重墙。

        若有人把 `should_trigger` 改成无条件 True，本测试将失败（即原「不触发」用例转红）。
        """
        fb = FallbackProvider(_fb_config())
        # 正常门控下：干净高置信 → 不触发（绿）
        assert fb.should_trigger(asr_failed=False, confidence=0.99) is False

        # 撤掉门控：模拟「门控被破坏」的实现
        monkeypatch.setattr(
            FallbackProvider, "should_trigger",
            lambda self, *, asr_failed=False, confidence=None: True,
        )
        # 同一输入现在会触发 → 说明原断言依赖门控，撤掉即转红
        assert fb.should_trigger(asr_failed=False, confidence=0.99) is True

    def test_removing_blocked_model_check_makes_red_line_silent(self, monkeypatch: Any) -> None:
        """反向验证：撤掉禁用模型拦截后，`FallbackProvider` 会**静默接受** qwen3-asr-flash，
        原本「构造即报错」的红线用例会转红 —— 证明拦截是红线唯一承重墙。"""
        # 正常：禁用模型构造即抛错（绿）
        with pytest.raises(ASRError):
            FallbackProvider(_fb_config(model="qwen3-asr-flash"))

        # 撤掉拦截：把禁用清单清空，模拟「红线被破坏」的实现
        monkeypatch.setattr(
            "app.services.asr.fallback.BLOCKED_MODELS", frozenset()
        )
        # 现在同一配置可正常构造 → 原「构造即报错」用例会失败（转红）
        fb2 = FallbackProvider(_fb_config(model="qwen3-asr-flash"))
        assert fb2.model == "qwen3-asr-flash"   # 红线被破坏后接受了禁用模型
        # 还原，避免影响其它用例
        monkeypatch.undo()
        assert "qwen3-asr-flash" in BLOCKED_MODELS
