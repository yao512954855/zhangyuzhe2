"""批次E 验收 —— 本地 ASR Provider（docs/localize-dev/13-后端-ASR-本地.md §6）。

全 Mock，**不起真模型**、不触网。覆盖：工厂注册 / 主备回落（hybrid 落云端、local 报错）/
拼读字母序列 / 中英转写 / 模型缺失抛 50006 / 懒加载 / used_provider 记录 / 音频规格校验。

运行：
    pytest app/tests/test_local_asr.py -q
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_local_asr_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from app.core.capability import engine_ready  # noqa: E402
from app.core.errors import ErrorCode  # noqa: E402
from app.services.asr import ASRFactory, ASRRouter, ASRTaskConfig  # noqa: E402
from app.services.asr.base import BYTES_PER_MS  # noqa: E402
from app.services.asr.local_provider import LocalProvider  # noqa: E402

CHUNK_150MS = b"\x00\x00" * (150 * BYTES_PER_MS // 2)   # 150ms @16k/16bit/mono


def _cfg(task_type: str = "spell", lang: str = "zh") -> ASRTaskConfig:
    return ASRTaskConfig(session_id=1, task_type=task_type, lang_hint=lang)  # type: ignore[arg-type]


def run(coro: Any) -> Any:
    import asyncio
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Mock 替身
# ---------------------------------------------------------------------------

class _FakeVoskRec:
    """Vosk Recognizer 替身，按脚本回放「帧 → 语句边界」时序。

    frames: `[(is_utterance_end, partial_text, utterance_text)]`，每次
    `AcceptWaveform` 消费一帧并返回是否到达语句边界；边界后 `Result()`
    返回该句确定文本（内部清空，再调返 ""）；`PartialResult` 返回当前帧
    的 partial；`FinalResult` 返回 close 时的残余文本。

    frames 留空 = 永远不到边界（旧行为：只有 close 时出 final），
    既有用例因此完全不受本次改动影响。
    """

    def __init__(
        self,
        final: str = "a p p l e",
        frames: list[tuple[bool, str, str]] | None = None,
    ) -> None:
        self._final = final
        self._frames = list(frames or [])
        self._pending_result = ""
        self._current_partial = ""

    def AcceptWaveform(self, pcm16: bytes) -> bool:  # noqa: N802 - 匹配 vosk 接口
        if not self._frames:
            return False
        end, partial, utext = self._frames.pop(0)
        self._current_partial = partial
        if end:
            self._pending_result = utext
        return end

    def PartialResult(self) -> str:  # noqa: N802
        return json.dumps({"text": self._current_partial})

    def Result(self) -> str:  # noqa: N802 - 本句确定结果
        # 真实 Vosk 语义：partial 有内容时 Result 即可把它定版（不必等
        # AcceptWaveform 返回 True）；定版后内部状态清空。
        text = self._pending_result or self._current_partial
        self._pending_result = ""
        self._current_partial = ""
        return json.dumps({"text": text})

    def FinalResult(self) -> str:  # noqa: N802
        return json.dumps({"text": self._final})


# ===========================================================================
# 工厂注册 & 主备编排
# ===========================================================================

class TestWiringAndRouter:
    def test_factory_creates_local(self) -> None:
        """型号注册生效：ASRFactory.create('local') 返回 LocalProvider。"""
        p = ASRFactory.create("local", {})
        assert isinstance(p, LocalProvider)
        assert p.name == "local"

    def test_hybrid_local_fails_falls_to_backup(self, monkeypatch) -> None:
        """hybrid：local start 抛 50006 → ASRRouter 落 aliyun（本例用 mock 充当云端）成功。"""
        import app.services.asr.local_provider as lp

        # 直接让 spell 通道的模型加载抛 50006（模拟模型未下载/加载异常）
        def _boom_load(*a, **k):
            from app.services.asr.base import ASRError
            from app.core.errors import ErrorCode as EC
            raise ASRError(EC.LOCAL_ENGINE_UNAVAILABLE, "mock 缺失", status="error")
        monkeypatch.setattr(lp, "_load_vosk_model", _boom_load)

        router = ASRRouter({"provider": "local", "backup_provider": "mock",
                             "mock": {"final_text": "classroom"}})
        finals: list[str] = []

        async def flow() -> None:
            p = await router.start(_cfg("spell", "zh"))
            p.on_final(lambda t, c, d: finals.append(t))
            await p.close()

        run(flow())
        assert finals == ["classroom"]
        assert router.used_provider == "mock"
        assert router.switched is True

    def test_local_mode_local_fails_raises(self, monkeypatch) -> None:
        """local 模式：local 失败 → 整场报错（无顺位可落）。"""
        import app.services.asr.local_provider as lp

        def _boom_load(*a, **k):
            from app.services.asr.base import ASRError
            from app.core.errors import ErrorCode as EC
            raise ASRError(EC.LOCAL_ENGINE_UNAVAILABLE, "mock 缺失", status="error")
        monkeypatch.setattr(lp, "_load_vosk_model", _boom_load)

        router = ASRRouter({"provider": "local", "backup_provider": ""})

        with pytest.raises(Exception):
            run(router.start(_cfg("spell", "zh")))


# ===========================================================================
# 引擎行为（Mock）
# ===========================================================================

class TestEngineBehavior:
    def test_spell_returns_letter_sequence(self, monkeypatch) -> None:
        """spell_zh2en：mock Vosk 返回 'a p p l e' → final 字母序列（与云端同链路口径）。"""
        import app.services.asr.local_provider as lp

        # ⚠️ _new_vosk_recognizer 是 LocalProvider 的类方法，必须 patch 类而不是模块
        monkeypatch.setattr(lp, "_load_vosk_model", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_vosk_recognizer",
                            lambda self, m: _FakeVoskRec("a p p l e"))

        p = LocalProvider({})
        finals: list[str] = []
        p.on_final(lambda t, c, d: finals.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("spell", "zh"))
            for _ in range(3):
                await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        assert finals == ["a p p l e"], "final 应为空格分隔小写字母序列，交给 _spell_feed 逐格判定"

    def test_spell_utterance_boundary_emits_without_end(self, monkeypatch) -> None:
        """zh2en 核心链路（2026-10-13 真机修复，契约 §5 修订 v1.0.3）：

        Vosk **语句边界**即产 utterance，不等 close/end —— App「看中文背
        英文」模式没有 end 入口，原实现判格只在 close 触发 → 字母格永远
        不亮，页面表现为卡死。念一个字母停一下 = 一个边界 = 一次判格。
        """
        import app.services.asr.local_provider as lp

        frames = [
            (False, "", ""),       # 帧1：开始念
            (False, "a", ""),      # 帧2：partial 出 'a'
            (True, "", "a"),       # 帧3：停一下 → 边界，本句 = 'a'
            (False, "", ""),       # 帧4：念下一个字母
            (True, "", "p"),       # 帧5：边界 'p'
        ]
        monkeypatch.setattr(lp, "_load_vosk_model", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_vosk_recognizer",
                            lambda self, m: _FakeVoskRec(final="", frames=frames))

        p = LocalProvider({})
        utterances: list[str] = []
        finals: list[str] = []
        p.on_utterance(lambda t, ts: utterances.append(t))
        p.on_final(lambda t, c, d: finals.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("spell", "zh"))
            for _ in range(5):
                await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        # 关键：utterance 在 close 之前就已产出（不等 end）
        assert utterances == ["a", "p"]
        # close 仍按 base 契约产 final（残余为空串）
        assert finals == [""]

    def test_spell_close_remainder_emits_utterance(self, monkeypatch) -> None:
        """兜底：一口气念完整词没触发边界 → close 的残余也走 utterance 判格
        （同时保持 close 必产 final 的 base 契约）。"""
        import app.services.asr.local_provider as lp

        frames = [(False, "a p p", "")] * 3     # 三段音频，始终没到边界
        monkeypatch.setattr(lp, "_load_vosk_model", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_vosk_recognizer",
                            lambda self, m: _FakeVoskRec(final="apple", frames=frames))

        p = LocalProvider({})
        utterances: list[str] = []
        finals: list[str] = []
        p.on_utterance(lambda t, ts: utterances.append(t))
        p.on_final(lambda t, c, d: finals.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("spell", "zh"))
            for _ in range(3):
                await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        assert utterances == ["apple"]
        assert finals == ["apple"]

    def test_spell_partial_still_emitted_between_boundaries(self, monkeypatch) -> None:
        """边界之间的 partial 链路不受影响（ utterance 是叠加通道，不是替换）。"""
        import app.services.asr.local_provider as lp

        frames = [
            (False, "a", ""),
            (True, "", "a"),
            (False, "p", ""),
        ]
        monkeypatch.setattr(lp, "_load_vosk_model", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_vosk_recognizer",
                            lambda self, m: _FakeVoskRec(final="", frames=frames))

        p = LocalProvider({})
        partials: list[str] = []
        p.on_partial(lambda t, ts: partials.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("spell", "zh"))
            for _ in range(3):
                await p.push_audio(CHUNK_150MS)

        run(flow())
        assert partials == ["a", "p"]

    def test_spell_silence_flush_emits_utterance(self, monkeypatch) -> None:
        """真人「念一个字母停一下」主路径：Vosk 内置 VAD 不触发边界时
        （2026-10-13 实测 22 条 qa 音频 0 次边界），服务端静音收口
        （partial 停止变化 ≥ SILENCE_FLUSH_MS）主动取 Result 判格。"""
        import app.services.asr.local_provider as lp

        # 帧1 出 partial 'a'；帧2~6 静音（partial 不变）→ 第 6 片
        # audio_ms=900 - last_voice=150 = 750 ≥ 700 触发收口
        frames = [(False, "a", "")] * 6
        monkeypatch.setattr(lp, "_load_vosk_model", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_vosk_recognizer",
                            lambda self, m: _FakeVoskRec(final="", frames=frames))

        p = LocalProvider({})
        utterances: list[str] = []
        p.on_utterance(lambda t, ts: utterances.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("spell", "zh"))
            for _ in range(6):
                await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        assert utterances == ["a"], "静音 700ms 后应主动收口，不等 close"

    def test_spell_no_flush_while_speaking(self, monkeypatch) -> None:
        """partial 持续变化（正在念）时不得收口 —— 防止把长词/连念切断
        造成字母错位。"""
        import app.services.asr.local_provider as lp

        # 5 帧 partial 一直在变（模拟一口气念 a→ap→app→appl→apple）
        frames = [
            (False, "a", ""), (False, "a p", ""), (False, "a p p", ""),
            (False, "a p p l", ""), (False, "a p p l e", ""),
        ]
        monkeypatch.setattr(lp, "_load_vosk_model", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_vosk_recognizer",
                            lambda self, m: _FakeVoskRec(final="", frames=frames))

        p = LocalProvider({})
        utterances: list[str] = []
        p.on_utterance(lambda t, ts: utterances.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("spell", "zh"))
            for _ in range(5):
                await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        assert utterances == [], "说话中途不得收口（close 残余也为空）"

    def test_non_spell_channel_no_utterance(self, monkeypatch) -> None:
        """sherpa（转写/en2zh/背诵）通道不产 utterance —— 新增通道对既有
        链路零影响（不注册回调时更是天然静默）。"""
        import app.services.asr.local_provider as lp

        monkeypatch.setattr(lp, "_load_sherpa_rec", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_sherpa_stream", lambda self, rec: object())
        monkeypatch.setattr(LocalProvider, "_sherpa_close_decode", lambda self: "苹果")

        p = LocalProvider({})
        utterances: list[str] = []
        p.on_utterance(lambda t, ts: utterances.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("recite", "zh"))
            for _ in range(3):
                await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        assert utterances == []

    def test_en2zh_returns_chinese_no_letter_frames(self, monkeypatch) -> None:
        """spell_en2zh/recite：mock sherpa 返回 '苹果' → final 为中文（网关不判分）。"""
        import app.services.asr.local_provider as lp

        # _load_sherpa_rec 是模块级函数可 patch 模块；流与解码是类方法，patch 类
        monkeypatch.setattr(lp, "_load_sherpa_rec", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_sherpa_stream", lambda self, rec: object())
        monkeypatch.setattr(LocalProvider, "_sherpa_close_decode", lambda self: "苹果")

        p = LocalProvider({})
        finals: list[str] = []
        p.on_final(lambda t, c, d: finals.append(t))

        async def flow() -> None:
            await p.start_session(_cfg("recite", "zh"))
            for _ in range(3):
                await p.push_audio(CHUNK_150MS)
            await p.close()

        run(flow())
        assert finals == ["苹果"]
        assert p.name == "local"

    def test_bad_audio_rejected_inside_provider(self, monkeypatch) -> None:
        """push_audio 必须先过 validate_pcm_chunk：非法音频抛 50004。"""
        import app.services.asr.local_provider as lp

        monkeypatch.setattr(lp, "_load_vosk_model", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_vosk_recognizer",
                            lambda self, m: _FakeVoskRec())

        p = LocalProvider({})

        async def flow() -> None:
            await p.start_session(_cfg("spell", "zh"))
            await p.push_audio(b"\x00\x00\x00")   # 奇数长度 → 非 16bit 采样

        with pytest.raises(Exception) as ei:
            run(flow())
        assert "50004" in str(ei.value.code) or ei.value.code == ErrorCode.ASR_BAD_AUDIO


# ===========================================================================
# 模型缺失 / 懒加载 / 探活
# ===========================================================================

class TestModelLifecycle:
    @pytest.fixture(autouse=True)
    def _reset_singletons(self, monkeypatch):
        """全量回归时其他用例可能已加载真模型单例，这里强制归零保证可复现。"""
        import app.services.asr.local_provider as lp

        monkeypatch.setattr(lp, "_vosk_model", None)
        monkeypatch.setattr(lp, "_sherpa_rec", None)

    def test_missing_model_raises_50006_spell(self, monkeypatch) -> None:
        import app.services.asr.local_provider as lp

        monkeypatch.setattr(lp, "_asr_model_dirs", lambda: (None, None))

        p = LocalProvider({})

        async def flow() -> None:
            await p.start_session(_cfg("spell", "zh"))

        with pytest.raises(Exception) as ei:
            run(flow())
        assert ei.value.code == ErrorCode.LOCAL_ENGINE_UNAVAILABLE

    def test_missing_model_raises_50006_recite(self, monkeypatch) -> None:
        import app.services.asr.local_provider as lp

        monkeypatch.setattr(lp, "_asr_model_dirs", lambda: (None, None))

        p = LocalProvider({})

        async def flow() -> None:
            await p.start_session(_cfg("recite", "zh"))

        with pytest.raises(Exception) as ei:
            run(flow())
        assert ei.value.code == ErrorCode.LOCAL_ENGINE_UNAVAILABLE

    def test_lazy_load_no_model_load_on_ready(self, monkeypatch) -> None:
        """懒加载：调 engine_ready('asr') 不加载模型（只查文件/import）。"""
        import app.services.asr.local_provider as lp

        # 确保不会真的去加载
        monkeypatch.setattr(lp, "_load_vosk_model", lambda: (_ for _ in ()).throw(
            RuntimeError("不应在探活时加载")))
        monkeypatch.setattr(lp, "_load_sherpa_rec", lambda: (_ for _ in ()).throw(
            RuntimeError("不应在探活时加载")))

        ready, detail = engine_ready("asr")
        assert isinstance(ready, bool)
        # 探活不得触发模型单例加载
        assert lp._vosk_model is None
        assert lp._sherpa_rec is None


# ===========================================================================
# used_provider 记录（asr_logs.provider 取此值）
# ===========================================================================

class TestUsedProvider:
    def test_used_provider_recorded_local(self, monkeypatch) -> None:
        """本地 provider 成功 → ASRRouter.used_provider == 'local'。"""
        import app.services.asr.local_provider as lp

        monkeypatch.setattr(lp, "_load_vosk_model", lambda: object())
        monkeypatch.setattr(LocalProvider, "_new_vosk_recognizer",
                            lambda self, m: _FakeVoskRec())

        router = ASRRouter({"provider": "local", "backup_provider": "mock",
                            "mock": {"final_text": "x"}})
        used: list[Any] = []

        async def flow() -> None:
            prov = await router.start(_cfg("spell", "zh"))
            used.append(prov)
            await prov.close()

        run(flow())
        assert router.used_provider == "local"
        # asr_logs 落库链路取 ASRRouter.used_provider（现有实现）
        assert router.used_provider == used[0].name
