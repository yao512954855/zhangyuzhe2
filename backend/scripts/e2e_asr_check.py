"""端到端语音链路自检：建真实场次 → WS start(带 mode) → 推真人 apple 录音 → end →
验证 verdict 携带真实 answer_id 且 answers 表已落库。

用法：`env -u PYTHONPATH .venv/Scripts/python.exe scripts/e2e_asr_check.py`
"""
import asyncio
import json
import sys
import wave

import httpx
import websockets

sys.path.insert(0, ".")
from app.core.security import create_access_token  # noqa: E402

WAV = "../qa/audio/apple__whole.wav"
BASE = "http://127.0.0.1:8810"
WS_BASE = "ws://127.0.0.1:8810"
STUDENT_ID = 2


def load_pcm_16k() -> bytes:
    """24k 单声道 16bit → 16k（每 3 个样本取 2 个，线性足够本验证用）。"""
    with wave.open(WAV) as w:
        frames = w.readframes(w.getnframes())
    samples = [int.from_bytes(frames[i : i + 2], "little", signed=True) for i in range(0, len(frames) - 1, 2)]
    out = []
    for i in range(0, len(samples) - 2, 3):
        out.append(samples[i])
        out.append((samples[i + 1] + samples[i + 2]) // 2)
    return b"".join(v.to_bytes(2, "little", signed=True) for v in out)


async def main() -> None:
    token, _ = create_access_token(user_id=STUDENT_ID, role="student")
    headers = {"Authorization": f"Bearer {token}"}

    # 1) 建真实练习场次（§9.1）
    async with httpx.AsyncClient(timeout=15, base_url=BASE) as client:
        r = await client.post(
            "/api/sessions",
            json={"unit_id": 1, "content_type": "english", "strategy": {}},
            headers=headers,
        )
        body = r.json()
        assert body.get("code") == 0, f"建场次失败：{body}"
        data = body["data"]
        session_id = data["session_id"]
        # 本脚本固定用 apple 的真人录音 → 优先挑 apple 那道题（word_id=2）
        item = next((i for i in data["items"] if i.get("word_id") == 2), data["items"][0])
        print(f"场次 {session_id} 建立，第 1 题 word_id={item['word_id']} mode={item['mode']}")

    pcm = load_pcm_16k()
    print(f"PCM 长度: {len(pcm)} 字节 ≈ {len(pcm) // 32}ms @16k")

    # 2) WS 语音判分（start 带 mode，模拟新版 App）
    async with websockets.connect(f"{WS_BASE}/ws/asr?token={token}") as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "start",
                    "session_id": session_id,
                    "task_type": "word",
                    "word_id": item["word_id"],
                    "mode": item.get("mode") or "cn2en",
                    "retry_count": 0,
                    "hint_level": 0,
                    "used_hint_count": 0,
                    "enable_intermediate": True,
                }
            )
        )
        print("→ start 已发送，等 started ...")
        print("←", await asyncio.wait_for(ws.recv(), timeout=10))

        for i in range(0, len(pcm), 4800):
            await ws.send(pcm[i : i + 4800])
            await asyncio.sleep(0.15)
        print("→ 音频全部推完，发 end")

        await ws.send(json.dumps({"type": "end"}))
        deadline = asyncio.get_event_loop().time() + 30
        while True:
            remain = deadline - asyncio.get_event_loop().time()
            if remain <= 0:
                print("!! 30 秒内未收到 verdict/error")
                return
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remain)
            except asyncio.TimeoutError:
                print("!! 超时")
                return
            if isinstance(msg, bytes):
                continue
            data = json.loads(msg)
            print("←", json.dumps(data, ensure_ascii=False)[:300])
            if data.get("type") in ("verdict", "error"):
                verdict = data
                break

    if verdict.get("type") != "verdict":
        print("!! 未收到 verdict")
        return

    # 3) 验证 answers 落库 + answer_id 回传
    answer_id = verdict.get("answer_id")
    print(f"verdict.answer_id = {answer_id}")
    if answer_id is None:
        print("!! answer_id 为空 —— 落库链路未生效")
        return
    import sqlite3

    conn = sqlite3.connect("data/bjsys.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM answers WHERE id = ?", (answer_id,)).fetchone()
    if row is None:
        print("!! answers 表没有对应行")
        return
    print(
        "answers 落库 OK:",
        {k: row[k] for k in ("session_id", "word_id", "mode", "step", "expected", "asr_text", "is_correct", "match_type", "used_keyboard")},
    )


asyncio.run(main())
