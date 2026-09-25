"""WP-BE-06 语音网关：Provider / 工厂 / 主备 / 仲裁 / 音频规格 —— 验收测试。

覆盖 10b 的 U5（音频规格）、U9（主备切换）、U14（`expected` 不外发）、
U15（不落盘）以及这些功能的边界：禁用模型拦截、语种映射、热词一期空、日志状态归一化。

真机端到端（真 uvicorn + 真 TCP WebSocket）在 `backend/scripts/smoke_be06.py`；
本文件是纯单元/轻量集成，秒级反馈。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be06f_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "be06-factory-secret-0123456789"
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "be06-Admin-Pass"
os.environ["LOG_LEVEL"] = "WARNING"

import yaml  # noqa: E402

from app.core.config import reset_config_cache  # noqa: E402
from app.core.errors import ErrorCode  # noqa: E402
from app.services.asr import (  # noqa: E402
    ASRError,
    ASRFactory,
    ASRRouter,
    ASRTaskConfig,
    AliyunProvider,
    ArbitratorProvider,
    MockProvider,
    VolcengineProvider,
    build_hotwords,
    load_asr_raw,
    validate_pcm_chunk,
)
from app.services.asr.base import BYTES_PER_MS, pcm_duration_ms  # noqa: E402

CHUNK_150MS = b"\x00\x00" * (150 * BYTES_PER_MS // 2)   # 150ms @16k/16bit/mono


def _cfg(task_type: str = "word", lang: str = "en") -> ASRTaskConfig:
    return ASRTaskConfig(session_id=1, task_type=task_type, lang_hint=lang)  # type: ignore[arg-type]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ===========================================================================
# 音频规格（F6 / U5）
# ===========================================================================

class TestAudioSpec:
    def test_valid_chunk_passes(self) -> None:
        validate_pcm_chunk(CHUNK_150MS)          # 不抛即通过
        assert pcm_duration_ms(len(CHUNK_150MS)) == 150

    def test_empty_chunk_is_skipped(self) -> None:
        validate_pcm_chunk(b"")                  # 空帧不算错误

    def test_bad_audio_raises_50004(self) -> None:
        """U5：非法音频必须**明确**报 50004，不能静默返回空。"""
        cases: dict[str, bytes] = {
            "odd_length": b"\x00\x00\x00",              # 16bit 采样不可能有半字节
            "wav_container": b"RIFF" + b"\x00" * 40,    # 应传裸 PCM，不该带容器头
            "ogg_container": b"OggS" + b"\x00" * 40,
            "mp3_tag": b"ID3" + b"\x00" * 40,
            "mp3_sync": b"\xff\xfb" + b"\x00" * 40,
            "too_large": b"\x00\x00" * 40000,           # 80000 字节 > 1000ms 上限
        }
        for why, chunk in cases.items():
            with pytest.raises(ASRError) as ei:
                validate_pcm_chunk(chunk)
            assert ei.value.code == ErrorCode.ASR_BAD_AUDIO, why
            assert ei.value.status == "error", why


# ===========================================================================
# MockProvider（F4）
# ===========================================================================

class TestMockProvider:
    def test_normal_flow_emits_partials_then_final(self) -> None:
        p = MockProvider({"final_text": "classroom", "partial_every": 1})
        partials: list[tuple[str, int]] = []
        finals: list[tuple[str, float | None, int]] = []
        p.on_partial(lambda t, ts: partials.append((t, ts)))
        p.on_final(lambda t, c, d: finals.append((t, c, d)))

        async def flow() -> None:
            await p.start_session(_cfg())
            for _ in range(5):
                await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        assert partials, "应当产出中间结果（语文逐字点亮依赖它）"
        # 累计语义：后一个必须是前一个的前缀扩展
        assert partials[-1][0].startswith(partials[0][0])
        assert finals == [("classroom", 0.95, 5 * 150)]

    def test_partial_text_is_cumulative_for_chinese(self) -> None:
        """中文按**字**累进（逐字点亮的最小粒度就是字），且最后一个 partial 不与 final 重复。"""
        p = MockProvider({"final_text": "我与父亲"})
        got: list[str] = []
        finals: list[str] = []
        p.on_partial(lambda t, ts: got.append(t))
        p.on_final(lambda t, c, d: finals.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("recite", "zh"))
            for _ in range(6):
                await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        assert got == ["我", "我与", "我与父"]
        for a, b in zip(got, got[1:]):
            assert b.startswith(a), "partial 必须是累计文本（ReciteEngine.feed 吃累计语义）"
        assert finals == ["我与父亲"]

    def test_empty_behavior_final_is_empty(self) -> None:
        p = MockProvider({"behavior": "empty"})
        finals: list[str] = []
        p.on_final(lambda t, c, d: finals.append(t))

        async def flow() -> None:
            await p.start_session(_cfg())
            await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        assert finals == [""]

    def test_timeout_behavior_raises_50002_with_keyboard(self) -> None:
        p = MockProvider({"behavior": "timeout"})

        async def flow() -> None:
            await p.start_session(_cfg())
            await p.close()

        with pytest.raises(ASRError) as ei:
            run(flow())
        assert ei.value.code == ErrorCode.ASR_TIMEOUT
        assert ei.value.status == "timeout"
        assert ei.value.action == "keyboard"

    def test_connect_fail_raises_before_audio(self) -> None:
        p = MockProvider({"behavior": "connect_fail"})

        async def flow() -> None:
            await p.start_session(_cfg())

        with pytest.raises(ASRError) as ei:
            run(flow())
        assert ei.value.code == ErrorCode.ASR_UNAVAILABLE
        assert p.start_calls == 1

    def test_fail_times_only_fails_first_n(self) -> None:
        p = MockProvider({"fail_times": 1, "final_text": "ok"})
        finals: list[str] = []
        p.on_final(lambda t, c, d: finals.append(t))

        async def flow() -> int:
            ok = 0
            for _ in range(2):
                try:
                    await p.start_session(_cfg())
                    ok += 1
                    await p.close()
                except ASRError:
                    continue
            return ok

        assert run(flow()) == 1
        assert finals == ["ok"]

    def test_abort_suppresses_final(self) -> None:
        p = MockProvider({"final_text": "should-not-arrive"})
        finals: list[str] = []
        p.on_final(lambda t, c, d: finals.append(t))

        async def flow() -> None:
            await p.start_session(_cfg())
            await p.push_audio(CHUNK_150MS)
            await p.abort()

        run(flow())
        assert finals == [], "cancel（abort）不得产出 final，也不应判分"

    def test_bad_audio_rejected_inside_provider(self) -> None:
        p = MockProvider({})

        async def flow() -> None:
            await p.start_session(_cfg())
            await p.push_audio(b"\x00\x00\x00")

        with pytest.raises(ASRError) as ei:
            run(flow())
        assert ei.value.code == ErrorCode.ASR_BAD_AUDIO

    def test_by_task_override(self) -> None:
        p = MockProvider(
            {
                "final_text": "classroom",
                "by_task": {"spell": {"final_text": "see ell aye ess ess"}},
            }
        )
        finals: list[str] = []
        p.on_final(lambda t, c, d: finals.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("spell", "zh"))
            await p.close()

        run(flow())
        assert finals == ["see ell aye ess ess"]


# ===========================================================================
# 工厂与主备（F5 / U9）
# ===========================================================================

class TestFactory:
    def test_create_each_provider(self) -> None:
        assert isinstance(ASRFactory.create("mock", {}), MockProvider)
        assert isinstance(ASRFactory.create("aliyun", {}), AliyunProvider)
        assert isinstance(ASRFactory.create("volcengine", {}), VolcengineProvider)

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ASRError) as ei:
            ASRFactory.create("openai", {})
        assert "未知的 ASR provider" in ei.value.message

    def test_router_without_backup_uses_primary(self) -> None:
        router = ASRRouter({"provider": "mock", "mock": {"final_text": "x"}})

        async def flow() -> None:
            p = await router.start(_cfg())
            assert p.name == "mock"
            await p.close()

        run(flow())
        assert router.used_provider == "mock"
        assert not router.switched

    def test_router_fails_over_to_backup_when_primary_cannot_connect(self) -> None:
        """U9：主厂商建连失败 → 备厂商完成识别。"""
        raw = {
            "provider": "anthropic",          # 未知厂商 → 建连必失败
            "backup_provider": "mock",
            "mock": {"final_text": "classroom"},
        }
        router = ASRRouter(raw)
        finals: list[str] = []

        async def flow() -> None:
            p = await router.start(_cfg())
            p.on_final(lambda t, c, d: finals.append(t))
            await p.close()

        run(flow())
        assert finals == ["classroom"]
        assert router.used_provider == "mock"
        assert router.switched is True

    def test_router_sticky_switch_after_three_consecutive_failures(self) -> None:
        raw = {
            "provider": "mock",
            "backup_provider": "mock",
            "mock": {"final_text": "ok"},
        }
        router = ASRRouter(raw)
        for _ in range(2):
            router.note_failure()
        assert router.switched is False, "未达阈值不应切换"
        router.note_failure()
        assert router.switched is True
        # 成功一次即清零（但已粘性切换）
        router.note_ok()
        assert router.consecutive_failures == 0

    def test_router_all_providers_down_raises(self) -> None:
        router = ASRRouter({"provider": "nope", "backup_provider": "also-nope"})

        with pytest.raises(ASRError):
            run(router.start(_cfg()))


# ===========================================================================
# 阿里云 Provider：模型选择与禁用拦截（F2）
# ===========================================================================

class TestAliyunProvider:
    _RAW = {
        "aliyun": {
            "api_key": "sk-test",
            "streaming_model": "qwen-audio-3.1-asr-flash-streaming",
            "batch_model": "fun-asr-flash-2026-06-15",
            "ws_endpoint": "wss://example.invalid/ws",
            "http_endpoint": "https://example.invalid/http",
            "http_format": "wav",
        }
    }

    def test_recite_uses_streaming_model_word_uses_batch(self) -> None:
        """按场景双模型：背诵走流式（逐字点亮），说词/拼读走非流式（忠实）。"""
        p = AliyunProvider(self._RAW)
        assert p.select_model("recite") == "qwen-audio-3.1-asr-flash-streaming"
        assert p.select_model("word") == "fun-asr-flash-2026-06-15"
        assert p.select_model("spell") == "fun-asr-flash-2026-06-15"
        assert p.select_model("zh_answer") == "fun-asr-flash-2026-06-15"

    def test_disabled_model_is_hard_blocked(self) -> None:
        """⛔ qwen3-asr-flash 会把错读脑补成正确词 —— 配错必须直接报错。"""
        raw = {
            "aliyun": {
                "api_key": "sk-test",
                "batch_model": "qwen3-asr-flash",
                "streaming_model": "qwen-audio-3.1-asr-flash-streaming",
            }
        }
        p = AliyunProvider(raw)

        async def flow() -> None:
            await p.start_session(_cfg("word", "en"))

        with pytest.raises(ASRError) as ei:
            run(flow())
        assert "禁用清单" in ei.value.message

    def test_missing_api_key_raises_explicitly(self) -> None:
        p = AliyunProvider({"aliyun": {"api_key": "", "batch_model": "fun-asr-flash"}})

        with pytest.raises(ASRError) as ei:
            run(p.start_session(_cfg()))
        assert "api_key" in ei.value.message

    def test_missing_model_name_raises_explicitly(self) -> None:
        p = AliyunProvider({"aliyun": {"api_key": "sk-test", "batch_model": ""}})

        with pytest.raises(ASRError) as ei:
            run(p.start_session(_cfg()))
        assert "batch_model" in ei.value.message

    def test_http_response_parsing_two_shapes(self) -> None:
        """非流式响应结构有两种（选型报告 §3.1），解析都要能处理。"""
        deep = {"output": {"output": {"sentence": {"text": "C L A S S. ", "confidence": 0.9}}}}
        text, conf = AliyunProvider._parse_http(deep)
        assert text == "C L A S S." and conf == 0.9

        flat = {"output": {"sentence": {"text": "CLASS"}}}
        assert AliyunProvider._parse_http(flat) == ("CLASS", None)

        choices = {"choices": [{"message": {"content": [{"text": "CLASS"}]}}]}
        assert AliyunProvider._parse_http(choices) == ("CLASS", None)

        assert AliyunProvider._parse_http({}) == ("", None)

    def test_ws_event_parsing(self) -> None:
        evt = {"header": {"event": "result-generated"},
               "payload": {"output": {"sentence": {"text": "我与父亲"}}}}
        assert AliyunProvider._parse_ws_event(evt) == "我与父亲"
        assert AliyunProvider._parse_ws_event({"payload": {}}) == ""

    def test_no_audio_written_to_disk(self) -> None:
        """U15：非流式会把整段音频缓存在内存 —— 断言**没有任何音频文件落盘**。"""
        p = AliyunProvider({**self._RAW, "aliyun": {
            **self._RAW["aliyun"], "http_endpoint": "https://example.invalid/http"}})
        before = {f for f in _TMP_DIR.rglob("*")}

        async def flow() -> None:
            await p.start_session(_cfg())
            await p.push_audio(CHUNK_150MS)
            assert len(p._pcm) == len(CHUNK_150MS), "音频应只存在于内存"
            await p.abort()

        run(flow())
        assert p._pcm == bytearray(), "abort 后内存缓冲必须清空"
        assert {f for f in _TMP_DIR.rglob("*")} == before


# ===========================================================================
# 火山引擎（F3）
# ===========================================================================

class TestVolcengineProvider:
    def test_incomplete_credentials_error_names_missing_fields(self) -> None:
        p = VolcengineProvider({"volcengine": {"app_id": "a"}})

        with pytest.raises(ASRError) as ei:
            run(p.start_session(_cfg()))
        assert "access_token" in ei.value.message and "endpoint" in ei.value.message

    def test_unreachable_endpoint_gives_explicit_error(self) -> None:
        p = VolcengineProvider(
            {
                "volcengine": {
                    "app_id": "a",
                    "access_token": "t",
                    "endpoint": "wss://127.0.0.1:1/never",
                },
                "connect_timeout_ms": 700,
            }
        )

        with pytest.raises(ASRError) as ei:
            run(p.start_session(_cfg()))
        assert ei.value.code in (ErrorCode.ASR_UNAVAILABLE, ErrorCode.ASR_TIMEOUT)
        assert "火山引擎" in ei.value.message


# ===========================================================================
# 仲裁（F19）
# ===========================================================================

class TestArbitrator:
    def _arb(self, **over: Any) -> ArbitratorProvider:
        section = {"enabled": True, "model": "qwen3.5-omni-plus",
                   "api_key": "sk-x", "endpoint": "https://example.invalid/arb"}
        section.update(over)
        return ArbitratorProvider({"arbitrator": section})

    def test_disabled_never_triggers(self) -> None:
        arb = self._arb(enabled=False)
        assert arb.should_trigger("empty") is False
        assert arb.should_trigger("fuzzy") is False
        assert arb.should_trigger(None, appeal=True) is False

    @pytest.mark.parametrize("match_type", ["empty", "fuzzy"])
    def test_trigger_on_two_match_types(self, match_type: str) -> None:
        assert self._arb().should_trigger(match_type) is True

    def test_trigger_on_appeal(self) -> None:
        assert self._arb().should_trigger(None, appeal=True) is True

    @pytest.mark.parametrize("match_type", ["exact", "loose", "wrong", "timeout"])
    def test_never_trigger_on_other_results(self, match_type: str) -> None:
        """严禁进主链路 —— 只有 empty / fuzzy / 申诉 允许触发。"""
        assert self._arb().should_trigger(match_type) is False

    def test_no_api_key_means_no_trigger(self) -> None:
        assert self._arb(api_key="").should_trigger("empty") is False
        assert self._arb(endpoint="").should_trigger("fuzzy") is False

    def test_kind_by_model_name(self) -> None:
        assert self._arb(model="qwen3.5-omni-plus").kind == "multimodal"
        assert self._arb(model="qwen-audio-3.0-asr-flash").kind == "asr"

    def test_multimodal_payload_contains_fixed_prompt(self) -> None:
        from app.services.asr.arbitrator import ARBITRATION_PROMPT

        arb = self._arb()
        payload = arb._build_payload(CHUNK_150MS)
        content = payload["input"]["messages"][0]["content"]
        assert content[0]["audio"].startswith("data:audio/")
        assert content[1]["text"] == ARBITRATION_PROMPT
        # 固定提示词 + 零温度 → 降低成本与波动
        assert payload["parameters"]["temperature"] == 0.0

    def test_asr_kind_payload_has_no_prompt(self) -> None:
        arb = self._arb(model="qwen-audio-3.0-asr-flash")
        payload = arb._build_payload(CHUNK_150MS)
        assert "text" not in payload["input"]["messages"][0]["content"][0]
        assert payload["parameters"]["format"] == "wav"

    def test_http_failure_returns_empty_not_raise(self) -> None:
        """仲裁是旁路 —— 失败必须只返回空串，不能把主流程带崩。"""
        arb = self._arb()

        async def boom(payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
            raise RuntimeError("network down")

        arb._call = boom  # type: ignore[method-assign]
        assert run(arb.arbitrate(CHUNK_150MS)) == ""

    def test_successful_arbitration_returns_text(self) -> None:
        arb = self._arb()

        async def fake(payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
            return {"output": {"choices": [{"message": {"content": "C L A S S"}}]}}

        arb._call = fake  # type: ignore[method-assign]
        assert run(arb.arbitrate(CHUNK_150MS)) == "C L A S S"

    def test_empty_audio_short_circuits(self) -> None:
        assert run(self._arb().arbitrate(b"")) == ""


# ===========================================================================
# 热词（F11）与配置加载
# ===========================================================================

class TestHotwordsAndConfig:
    def test_hotwords_disabled_returns_empty(self) -> None:
        assert build_hotwords(["classroom", "class"], enabled=False) == []

    def test_hotwords_enabled_dedups(self) -> None:
        got = build_hotwords(["a", "b", "a"], enabled=True, sources=["b", "c"])
        assert got == ["a", "b", "c"]

    def test_load_asr_raw_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_asr_raw(tmp_path / "nope.yaml") == {}

    def test_load_asr_raw_env_override(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "custom.yaml"
        cfg.write_text(yaml.safe_dump({"provider": "mock"}), encoding="utf-8")
        monkeypatch.setenv("ASR_CONFIG_PATH", str(cfg))
        assert load_asr_raw()["provider"] == "mock"
        monkeypatch.delenv("ASR_CONFIG_PATH")
        reset_config_cache()
