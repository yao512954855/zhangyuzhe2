"""拼读识别诊断：同一批真人 spell 录音，Vosk(26字母) vs SenseVoice 对照。

用法：cd backend && env -u PYTHONPATH .venv/Scripts/python.exe scripts/_spell_diag.py
只读模型与 qa 夹具，不起端口，可与 dev 后端共存（不占 UDP 9527）。
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

QA = REPO / "qa" / "audio"
WORDS = ["apple", "bag", "beautiful", "bed", "breakfast", "bus", "cat",
         "class", "classroom", "banana", "orange"]


def load16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        pcm = np.frombuffer(w.readframes(n), dtype=np.int16)
    if w.getnchannels() == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
    if sr != VOSK_SR:
        # 简单线性重采样仅用于诊断夹具（均为干净录音）
        x = np.linspace(0, 1, int(len(pcm) * VOSK_SR / sr), endpoint=False)
        pcm = np.interp(x, np.linspace(0, 1, len(pcm)), pcm.astype(np.float64))
        pcm = pcm.astype(np.int16)
    return pcm


def vosk_letters(vosk_dir, pcm) -> str:
    from vosk import Model, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
    model = Model(str(vosk_dir))
    rec = KaldiRecognizer(model, VOSK_SR, GRAMMAR)
    rec.SetWords(False)
    chunk = 3200  # 200ms
    letters: list[str] = []
    for i in range(0, len(pcm), chunk):
        if rec.AcceptWaveform(pcm[i:i + chunk].tobytes()):
            letters += json.loads(rec.Result()).get("text", "").split()
    letters += json.loads(rec.FinalResult()).get("text", "").split()
    del model, rec
    return " ".join(letters)


def sensevoice(sherpa_dir, pcm) -> str:
    import sherpa_onnx
    rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(sherpa_dir / "model.int8.onnx"),
        tokens=str(sherpa_dir / "tokens.txt"),
        use_itn=True, num_threads=2, provider="cpu",
    )
    stream = rec.create_stream()
    stream.accept_waveform(VOSK_SR, pcm.astype(np.float32) / 32768.0)
    rec.decode_stream(stream)
    return stream.result.text.strip()


def main() -> None:
    vosk_dir, sherpa_dir = _asr_model_dirs()
    assert vosk_dir and sherpa_dir, "模型目录缺失"
    print(f"vosk={vosk_dir.name}\nsherpa={sherpa_dir.name}\n")

    # 只建一次模型实例，避免每个词重复加载
    from vosk import Model, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
    vmodel = Model(str(vosk_dir))
    import sherpa_onnx
    srec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(sherpa_dir / "model.int8.onnx"),
        tokens=str(sherpa_dir / "tokens.txt"),
        use_itn=True, num_threads=2, provider="cpu",
    )

    n_ok_v = n_ok_s = 0
    for word in WORDS:
        f = QA / f"{word}__spell.wav"
        if not f.exists():
            print(f"{word:<10} 夹具缺失，跳过")
            continue
        pcm = load16k(f)
        expect = " ".join(word)

        rec = KaldiRecognizer(vmodel, VOSK_SR, GRAMMAR)
        got_v = []
        for i in range(0, len(pcm), 3200):
            if rec.AcceptWaveform(pcm[i:i + 3200].tobytes()):
                got_v += json.loads(rec.Result()).get("text", "").split()
        got_v += json.loads(rec.FinalResult()).get("text", "").split()
        got_v = " ".join(got_v)

        stream = srec.create_stream()
        stream.accept_waveform(VOSK_SR, pcm.astype(np.float32) / 32768.0)
        srec.decode_stream(stream)
        got_s = stream.result.text.strip()

        ok_v = got_v.replace(" ", "") == word
        ok_s = got_s.replace(" ", "").lower() == word
        n_ok_v += ok_v
        n_ok_s += ok_s
        print(f"{word:<10} 期望[{expect}]  vosk[{got_v}]{'✓' if ok_v else '✗'}"
              f"  sensevoice[{got_s}]{'✓' if ok_s else '✗'}")

    print(f"\nVosk {n_ok_v}/{len(WORDS)}   SenseVoice {n_ok_s}/{len(WORDS)}")


if __name__ == "__main__":
    main()
