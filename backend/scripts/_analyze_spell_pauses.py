"""分析 spell 音频的停顿结构：每 150ms 片 RMS 包络 + 尾部静音时长。

目的：确认为什么 Vosk AcceptWaveform 从不返回 True（语句边界 0 次），
以及真人「念一个字母停一下」的节奏能否触发边界。
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent.parent
VOSK_SR = 16000
CHUNK_BYTES = VOSK_SR * 2 * 150 // 1000


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


def envelope(pcm16: np.ndarray) -> list[float]:
    out = []
    for off in range(0, len(pcm16) * 2, CHUNK_BYTES):
        seg = pcm16[off // 2:(off // 2) + CHUNK_BYTES // 2]
        if len(seg) == 0:
            break
        out.append(float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))))
    return out


def main() -> int:
    files = sorted((REPO / "qa" / "audio").glob("*__spell.wav"))
    for f in files[:8]:
        sr, x = read_wav_int16(f)
        pcm16 = resample(x, sr, VOSK_SR)
        env = envelope(pcm16)
        dur_s = len(pcm16) / VOSK_SR
        peak = max(env) if env else 0
        # 静音阈值：峰值的 8%
        thr = peak * 0.08
        # 找语音段（连续 >= thr 的片）与停顿段
        voiced = [i for i, v in enumerate(env) if v >= thr]
        runs: list[tuple[int, int]] = []
        if voiced:
            start = prev = voiced[0]
            for i in voiced[1:]:
                if i == prev + 1:
                    prev = i
                else:
                    runs.append((start, prev))
                    start = prev = i
            runs.append((start, prev))
        # 尾静音
        tail_silence = (len(env) - 1 - (runs[-1][1] if runs else -1)) * 0.15
        seg_str = " ".join(f"[{a*0.15:.2f}-{b*0.15:.2f}]" for a, b in runs)
        print(f"{f.name:<28} 时长{dur_s:5.2f}s 峰{peak:7.0f} 语音段: {seg_str} 尾静音{tail_silence:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
