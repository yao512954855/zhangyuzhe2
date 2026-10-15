"""真实 Vosk 模型验证：语句边界实时判格链路（契约 §5 修订 v1.0.3）。

不起服务、不连云端，直接驱动真实 Vosk 模型，按网关同规格
（16k / 单声道 / 16bit，150ms/片）逐帧喂入 `*__spell.wav`，记录：

  1. 每片 AcceptWaveform 的返回值（语句边界）；
  2. 边界时 Result() 的确定文本（新链路判格来源）；
  3. 全程 PartialResult（对比：证明短词场景 partial 恒空、
    确定结果只在 Result —— 2026-10-13 真机「字母格不亮」根因）。

用法：
    python scripts/_verify_vosk_utterance.py [--audio-dir qa/audio]
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

LETTERS = [chr(ord("a") + i for _ in range(0))] if False else [chr(ord("a") + i) for i in range(26)]
GRAMMAR = json.dumps(LETTERS)
VOSK_SR = 16000
CHUNK_MS = 150
CHUNK_BYTES = VOSK_SR * 2 * CHUNK_MS // 1000      # 4800 B/片


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
    ap.add_argument("--audio-dir", default=str(REPO / "qa" / "audio"))
    ap.add_argument("--models-dir", default=str(BACKEND / "models" / "asr"))
    args = ap.parse_args()

    from vosk import Model, KaldiRecognizer

    model_dir = next((p for p in Path(args.models_dir).iterdir()
                      if p.is_dir() and p.name.startswith("vosk-model-small-en-us")), None)
    if model_dir is None:
        print("找不到 Vosk 模型目录")
        return 2
    model = Model(str(model_dir))

    files = sorted(Path(args.audio_dir).glob("*__spell.wav"))
    if not files:
        print(f"没有 spell 音频：{args.audio_dir}")
        return 2

    print(f"模型：{model_dir.name}  音频 {len(files)} 个（150ms/片，16k）\n")
    total = ok = 0
    boundary_hits = 0
    for f in files:
        target = f.name.split("__")[0]
        sr, x = read_wav_int16(f)
        pcm16 = resample(x, sr, VOSK_SR)
        rec = KaldiRecognizer(model, VOSK_SR, GRAMMAR)

        utterances: list[str] = []      # 边界时的 Result 文本（新链路判格来源）
        partials: list[str] = []        # 全程非空 partial（对比用）
        n_frames = 0
        for off in range(0, len(pcm16) * 2, CHUNK_BYTES):
            chunk = pcm16[off // 2:(off // 2) + CHUNK_BYTES // 2].tobytes()
            if not chunk:
                break
            n_frames += 1
            if rec.AcceptWaveform(chunk):          # 语句边界
                text = json.loads(rec.Result()).get("text", "").strip()
                if text:
                    utterances.append(text)
                    boundary_hits += 1
            else:
                part = json.loads(rec.PartialResult()).get("text", "").strip()
                if part:
                    partials.append(part)
        final = json.loads(rec.FinalResult()).get("text", "").strip()

        # 新链路效果：边界 utterance + close 残余 → 拼出的字母序列
        got = "".join(utterances) + final
        got_letters = "".join(c for c in got.lower() if c.isalpha())
        want = "".join(c for c in target.lower() if c.isalpha())
        hit = got_letters == want
        total += 1
        ok += int(hit)
        print(
            f"{'PASS' if hit else 'FAIL'} {target:<12} 边界={len(utterances)} "
            f"utt={utterances} partial非空={len(partials)} close残余={final!r} → {got_letters!r}"
        )

    print(f"\n合计 {ok}/{total} PASS；语句边界触发 {boundary_hits} 次")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
