"""本地化端到端冒烟：hybrid 模式下真引擎全链路（不起 pytest、直连 8810 dev 后端）。

覆盖：
  A. POST /api/tts               → 本地 melo 合成（hybrid=local 优先）
  B. WS spell_zh2en（Vosk 字母） → qa 真人 apple 拼读录音 → letter 帧 + verdict
  C. WS spell_en2zh（SenseVoice）→ 本地 melo 合成「苹果。」→ final 转写中文
  D. POST /api/practice/answer   → 转写文本判分 correct=true

用法：`env -u PYTHONPATH .venv/Scripts/python.exe scripts/smoke_local_asr.py`
前置：dev 后端已起（8810）、模型已就位。
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import wave
from pathlib import Path

import httpx
import numpy as np
import websockets

BASE = "http://127.0.0.1:8810"
WS_BASE = "ws://127.0.0.1:8810"
REPO = Path(__file__).resolve().parent.parent.parent
SPELL_WAV = REPO / "qa" / "audio" / "apple__spell.wav"


def resample_aa(pcm: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """FFT 低通抗混叠重采样（冒烟脚本用；业务代码禁止自写降采样，见 local_provider）。"""
    x = pcm.astype(np.float64)
    if sr_in > sr_out:  # 先低通到 sr_out/2，再线性插值
        spec = np.fft.rfft(x)
        freqs = np.fft.rfftfreq(len(x), 1 / sr_in)
        spec[freqs > sr_out / 2 * 0.95] = 0
        x = np.fft.irfft(spec, len(x))
    n_out = max(1, int(round(len(x) * sr_out / sr_in)))
    out = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    return np.clip(out, -32768, 32767).astype(np.int16)


def wav_pcm(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path)) as w:
        return w.getframerate(), np.frombuffer(w.readframes(10**9), dtype=np.int16)


def melo_zh_pcm(text: str) -> np.ndarray:
    """本地 TTS 合成中文 → 16k PCM（FFT 抗混叠）。"""
    sys.path.insert(0, ".")
    from app.services import tts_local

    wav = tts_local.synthesize_local(text, lang="zh-CN")
    with wave.open(io.BytesIO(wav)) as r:
        sr = r.getframerate()
        pcm = np.frombuffer(r.readframes(10**9), dtype=np.int16)
    return resample_aa(pcm, sr, 16000)


async def ws_flow(mode: str, item_id: int, pcm: bytes, expect: dict) -> dict:
    session_id = f"smoke-{mode}-{item_id}"
    out: dict = {"letters": [], "finals": [], "verdicts": [], "errors": []}
    async with websockets.connect(f"{WS_BASE}/ws/asr?token=smoke") as ws:
        await ws.send(json.dumps({"type": "start", "mode": mode,
                                  "item_id": item_id, "session_id": session_id}))
        for i in range(0, len(pcm), 4800):
            await ws.send(pcm[i:i + 4800])
            await asyncio.sleep(0.06)
        await ws.send(json.dumps({"type": "end"}))
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                break
            if isinstance(msg, bytes):
                continue
            d = json.loads(msg)
            t = d.get("type")
            if t == "letter":
                out["letters"].append((d["index"], d["letter"], d["correct"]))
            elif t == "final":
                out["finals"].append(d.get("text", ""))
            elif t == "verdict":
                out["verdicts"].append(d)
            elif t == "error":
                out["errors"].append(d)
            if t in ("verdict", "error") and (t == "error" or out["verdicts"]):
                break
    return out


async def main() -> int:
    ok = True
    async with httpx.AsyncClient(timeout=20, base_url=BASE) as client:
        # A. 本地 TTS
        r = await client.post("/api/tts", json={"text": "苹果。", "lang": "zh-CN"})
        is_wav = r.headers.get("content-type", "").startswith("audio/wav")
        print(f"[A] /api/tts → {r.status_code} {r.headers.get('content-type')} "
              f"{len(r.content)}B  wav={is_wav}")
        ok &= is_wav and r.status_code == 200 and len(r.content) > 1000

        # 建场次拿 item
        r = await client.post("/api/practice/start",
                              json={"unit_id": 6, "type": "word", "mode": "spell_zh2en"})
        body = r.json()
        assert body.get("code") == 0, f"建场次失败：{body}"
        items = body["data"]["items"]
        apple = next(i for i in items if i["en"] == "apple")
        print(f"[S] 场次 {body['data']['session_id']}，apple item_id={apple['id']}")

        # B. Vosk 字母拼读（qa 真人录音 24k → 16k）
        sr, pcm = wav_pcm(SPELL_WAV)
        pcm16 = resample_aa(pcm, sr, 16000).tobytes()
        res = await ws_flow("spell_zh2en", apple["id"], pcm16, expect={})
        print(f"[B] spell_zh2en letters={res['letters']} verdicts={len(res['verdicts'])} "
              f"errors={res['errors'][:1]}")
        ok &= bool(res["letters"]) and not res["errors"]

        # C. SenseVoice 中文转写（melo 合成「苹果。」）
        zh_pcm = melo_zh_pcm("苹果。").tobytes()
        res = await ws_flow("spell_en2zh", apple["id"], zh_pcm, expect={})
        print(f"[C] spell_en2zh finals={res['finals']} errors={res['errors'][:1]}")
        ok &= any("苹果" in f for f in res["finals"]) and not res["errors"]
        zh_text = next((f for f in res["finals"] if "苹果" in f), "苹果")

        # D. 判分
        r = await client.post("/api/practice/answer", json={
            "session_id": body["data"]["session_id"], "item_id": apple["id"],
            "mode": "spell_en2zh", "answer": zh_text, "elapsed_ms": 1000})
        ans = r.json()
        print(f"[D] answer → {ans.get('code')} correct={ans.get('data', {}).get('correct')}")
        ok &= ans.get("code") == 0 and ans.get("data", {}).get("correct") is True

    print("\n冒烟结论:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
