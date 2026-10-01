"""临时：逐帧追踪语文流式背诵的对齐推进（partial 文本 + 引擎 pos/matched）。

跑法：`env -u PYTHONPATH .venv/Scripts/python.exe scripts/_cn_trace.py`
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

BASE = "http://127.0.0.1:8810"
WS = "ws://127.0.0.1:8810/ws/asr"
ROOT = Path(__file__).resolve().parents[2]
PCM = Path(os.environ.get("CN_PCM", str(ROOT / "qa" / "audio" / "_cn_test" / "beijing_16k.pcm")))
CHUNK = 4800
CHUNK_MS = 150


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def main() -> int:
    pcm = PCM.read_bytes()
    print(f"音频：{len(pcm)} 字节 ≈ {len(pcm) / 32000:.2f}s")

    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        uname = f"A{random.randint(10_000_000, 99_999_999)}"
        r = await c.post(
            "/api/auth/register",
            json={"username": uname, "real_name": "追踪", "password": "test1234"},
        )
        j = r.json()
        assert j.get("code") == 0, j
        token = j["data"]["access_token"]

        r = await c.post(
            "/api/sessions", json={"unit_id": 3, "content_type": "chinese"}, headers=_auth(token)
        )
        j = r.json()
        assert j.get("code") == 0, j
        sid = j["data"]["session_id"]
        text_id = int(j["data"]["items"][0]["text_id"])

        r = await c.post(
            "/api/recitations",
            json={"session_id": sid, "text_id": text_id, "scope": [0, 1]},
            headers=_auth(token),
        )
        j = r.json()
        assert j.get("code") == 0, j
        rid = int(j["data"]["recitation_id"])
        print(f"session={sid} recitation={rid} total_chars={j['data'].get('total_chars')}\n")

        rows: list[dict] = []
        finals: list[dict] = []

        async with connect(f"{WS}?token={token}", max_size=None, open_timeout=15) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "start",
                        "task_type": "recite",
                        "session_id": sid,
                        "recitation_id": rid,
                    },
                    ensure_ascii=False,
                )
            )
            await asyncio.wait_for(ws.recv(), timeout=20)

            async def recv_loop() -> None:
                while True:
                    try:
                        raw = await ws.recv()
                    except Exception:
                        return
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    t = m.get("type")
                    if t == "partial":
                        rows.append(m)
                    elif t in ("final", "verdict"):
                        finals.append(m)
                        if t == "final":
                            rows.append({"type": "FINAL", **m})
                    elif t == "error":
                        finals.append(m)

            task = asyncio.create_task(recv_loop())

            for i in range(0, len(pcm), CHUNK):
                await ws.send(pcm[i : i + CHUNK])
                await asyncio.sleep(CHUNK_MS / 1000)
            await ws.send(json.dumps({"type": "end"}))

            deadline = time.time() + 25
            while time.time() < deadline and not finals:
                await asyncio.sleep(0.2)
            await asyncio.sleep(1.0)
            task.cancel()
            try:
                await task
            except BaseException:
                pass

        print("=" * 78)
        print("帧 | 累计文本 | pos | matched")
        print("-" * 78)
        for i, m in enumerate(rows, 1):
            txt = m.get("text") or ""
            rc = m.get("recite") or {}
            print(f"{i:>3} | pos={rc.get('pos')!s:>4} matched={rc.get('matched_chars')!s:>4} "
                  f"| {txt!r}")
            if m.get("type") == "FINAL":
                print("     ^^^ FINAL")
        print("=" * 78)
        for f in finals:
            if f.get("type") == "verdict":
                rc = f.get("recite") or {}
                print("verdict.recite:", json.dumps(
                    {k: rc.get(k) for k in
                     ("pos", "total_chars", "matched_chars", "remaining", "restart_count")},
                    ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
