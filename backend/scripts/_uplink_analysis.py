"""真机上行音频分析：音量/削波/静音 + Vosk 离线复现识别。

用法：cd backend && env -u PYTHONPATH .venv/Scripts/python.exe scripts/_uplink_analysis.py
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.core.config import BASE_DIR  # noqa: E402
from app.services.asr.local_provider import (  # noqa: E402
    GRAMMAR, VOSK_SR, _asr_model_dirs,
)

QA = REPO / "qa"


def vosk_transcribe(vmodel, pcm) -> list[str]:
    from vosk import KaldiRecognizer
    rec = KaldiRecognizer(vmodel, VOSK_SR, GRAMMAR)
    out: list[str] = []
    for i in range(0, len(pcm), 3200):
        if rec.AcceptWaveform(pcm[i:i + 3200].tobytes()):
            out += json.loads(rec.Result()).get("text", "").split()
    out += json.loads(rec.FinalResult()).get("text", "").split()
    return out


def main() -> None:
    from vosk import Model, SetLogLevel
    SetLogLevel(-1)
    vosk_dir, _ = _asr_model_dirs()
    vmodel = Model(str(vosk_dir))

    files = sorted(QA.glob("_app_uplink_*.wav"))
    for f in files:
        with wave.open(str(f), "rb") as w:
            sr = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        x = pcm.astype(np.float64) / 32768.0

        # 分帧（30ms）能量
        frame = int(VOSK_SR * 0.03)
        n_frames = len(x) // frame
        rms = np.sqrt(np.mean(x[:n_frames * frame].reshape(n_frames, frame) ** 2, axis=1))
        peak = np.max(np.abs(x))
        clip = float(np.mean(np.abs(pcm) >= 32000)) * 100
        # 静音帧占比（RMS < 1% 满量程）
        silent = float(np.mean(rms < 0.01)) * 100
        # 有声段占比
        voiced = float(np.mean(rms > 0.02)) * 100

        letters = vosk_transcribe(vmodel, pcm)
        print(f"== {f.name}  {len(pcm)/VOSK_SR:.2f}s  sr={sr}")
        print(f"   峰值={peak:.3f}  削波帧占比={clip:.2f}%  "
              f"静音帧={silent:.0f}%  有声帧={voiced:.0f}%  "
              f"RMS中位={np.median(rms):.3f}")
        print(f"   Vosk 离线复现: {' '.join(letters)}")
    print("\n（对照：服务端在线判定结果见后端日志）")


if __name__ == "__main__":
    main()
