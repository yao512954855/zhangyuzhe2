"""端到端验证：带停顿的字母音频 → 新 LocalProvider → utterance 判格。

复刻真机场景：把 qa 的 spell 音频按字母切开、段间插入静音（模拟
「念一个字母停一下」），直接驱动 LocalProvider 真实代码路径，
打印 utterance / partial 事件时间线。

用法：
    python scripts/_verify_pause_spell.py [--silence-ms 800]
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent.parent
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VOSK_SR = 16000
CHUNK_BYTES = VOSK_SR * 2 * 150 // 1000      # 4800 B/片（150ms）

import os
os.environ.setdefault("DB_PATH", str(BACKEND / "data" / "bjsys.db"))


def read_wav_int16(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    return sr, np.frombuffer(raw, dtype=np.int16)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    n_out = max(1, int(round(len(x) * sr_out / sr_in)))
    return np.clip(np.interp(np.linspace(0, len(x) - 1, n_out),
                             np.arange(len(x)), x.astype(np.float64)),
                   -32768, 32767).astype(np.int16)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--word", default="cat")
    ap.add_argument("--letters", default="3", help="字母段数")
    ap.add_argument("--silence-ms", type=int, default=800)
    args = ap.parse_args()

    from app.services.asr import local_provider as lp_mod
    from app.services.asr.base import ASRTaskConfig
    from app.services.asr.local_provider import LocalProvider

    src = REPO / "qa" / "audio" / f"{args.word}__spell.wav"
    sr, x = read_wav_int16(src)
    pcm16 = resample(x, sr, VOSK_SR)
    n_seg = int(args.letters)
    # 切掉尾部静音，只取语音段，再按字母数均分
    env = [float(np.sqrt(np.mean(pcm16[i:i + 4800].astype(np.float64) ** 2)))
           for i in range(0, len(pcm16) - 4800, 4800)]
    peak = max(env)
    voiced = [i for i, v in enumerate(env) if v >= peak * 0.08]
    end_sample = (voiced[-1] + 1) * 4800 if voiced else len(pcm16)
    speech = pcm16[:end_sample]
    seg_len = len(speech) // n_seg
    silence = np.zeros(VOSK_SR * args.silence_ms // 1000, dtype=np.int16)

    p = LocalProvider({})
    events: list[tuple[float, str, str]] = []   # (ms, kind, text)

    def on_utt(t, ts):
        events.append((p.audio_ms, "UTT", t))

    def on_part(t, ts):
        events.append((p.audio_ms, "partial", t))

    p.on_utterance(on_utt)
    p.on_partial(on_part)

    import asyncio

    async def flow() -> None:
        cfg = ASRTaskConfig(session_id=1, task_type="spell", lang_hint="zh")
        await p.start_session(cfg)
        for i in range(n_seg):
            seg = speech[i * seg_len:(i + 1) * seg_len]
            buf = np.concatenate([seg, silence])
            for off in range(0, len(buf) * 2, CHUNK_BYTES):
                chunk = buf[off // 2:(off // 2) + CHUNK_BYTES // 2].tobytes()
                if chunk:
                    await p.push_audio(chunk)
        await p.close()

    asyncio.run(flow())

    print(f"音频：{src.name} 切成 {n_seg} 段，段间静音 {args.silence_ms}ms")
    print(f"SILENCE_FLUSH_MS = {lp_mod.SILENCE_FLUSH_MS}\n")
    for ms, kind, text in events:
        print(f"  t={ms:5d}ms  {kind:8s} {text!r}")
    utts = [t for _, k, t in events if k == "UTT"]
    got = "".join(c for c in "".join(utts).lower() if c.isalpha())
    print(f"\nutterance 共 {len(utts)} 次 → 字母序列 {got!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
