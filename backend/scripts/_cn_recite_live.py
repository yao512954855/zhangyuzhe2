"""语文（中文背诵）流式链路真机前自检 —— 临时脚本。

目的：证明 **服务端 WS 中转 + 云端流式 ASR（qwen-audio-…-streaming）** 这条
从没在真实环境跑过的路能通，并在 `asr_logs` 落一条 `lang=zh / task_type=recite`。

做法：直接吃 `qa/audio/_cn_test/beijing_16k.pcm`（edge-tts 生成的课文音频，
16k/单声道/16bit，与 App 推给云端的格式逐项一致），按 150ms 一包推。

跑法：`env -u PYTHONPATH .venv/Scripts/python.exe scripts/_cn_recite_live.py`
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

# sandbox shell 带 HTTP_PROXY，直连本机/云端都不该走它（会 502）。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

BASE = "http://127.0.0.1:8810"
WS = "ws://127.0.0.1:8810/ws/asr"
ROOT = Path(__file__).resolve().parents[2]
PCM = Path(
    os.environ.get("CN_PCM", str(ROOT / "qa" / "audio" / "_cn_test" / "beijing_16k.pcm"))
)
CHUNK = 4800           # 150ms @ 16k/16bit/mono
CHUNK_MS = 150


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def main() -> int:
    if not PCM.exists():
        print(f"!! 找不到音频：{PCM}")
        return 2
    pcm = PCM.read_bytes()
    print(f"音频：{len(pcm)} 字节 ≈ {len(pcm) / 32000:.2f}s")

    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        # ---- 1. 注册一个临时学生（自主注册，无需审核、直接发 token）----
        uname = f"A{random.randint(10_000_000, 99_999_999)}"
        r = await c.post(
            "/api/auth/register",
            json={"username": uname, "real_name": "语文联调", "password": "test1234"},
        )
        j = r.json()
        if j.get("code") != 0:
            print("!! 注册失败：", json.dumps(j, ensure_ascii=False))
            return 2
        token = j["data"]["access_token"]
        uid = j["data"]["user"]["id"]
        print(f"[1] 注册 ok  user={uid} ({uname})")

        # ---- 2. 建语文场次（unit 3 = 三年级 语文 第一单元）----
        r = await c.post(
            "/api/sessions",
            json={"unit_id": 3, "content_type": "chinese"},
            headers=_auth(token),
        )
        j = r.json()
        if j.get("code") != 0:
            print("!! 建场次失败：", json.dumps(j, ensure_ascii=False))
            return 2
        data = j["data"]
        sid = data["session_id"]
        items = data.get("items") or []
        print(f"[2] 语文场次 ok  session_id={sid}  题量={data.get('total_items')}")
        for it in items:
            print("      item:", json.dumps(it, ensure_ascii=False))
        if not items:
            print("!! 场次没有课文")
            return 2
        text_id = int(items[0]["text_id"])

        # ---- 3. 建背诵记录 ----
        r = await c.post(
            "/api/recitations",
            json={"session_id": sid, "text_id": text_id, "scope": [0, 1]},
            headers=_auth(token),
        )
        j = r.json()
        if j.get("code") != 0:
            print("!! 建背诵失败：", json.dumps(j, ensure_ascii=False))
            return 2
        rdata = j["data"]
        rid = int(rdata["recitation_id"])
        print(f"[3] 背诵记录 ok  recitation_id={rid}  total_chars={rdata.get('total_chars')}")

        # ---- 4. WS 走真流式链路 ----
        url = f"{WS}?token={token}"
        partials: list[dict] = []
        finals: list[dict] = []
        errors: list[dict] = []
        t_start = time.time()
        first_partial_ms: float | None = None
        first_partial_text = ""

        async with connect(url, max_size=None, open_timeout=15) as ws:
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
            started = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            print("[4] started:", json.dumps(started, ensure_ascii=False))
            if started.get("type") == "error":
                errors.append(started)

            async def recv_loop() -> None:
                nonlocal first_partial_ms, first_partial_text
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
                        if first_partial_ms is None:
                            first_partial_ms = (time.time() - t_start) * 1000
                            first_partial_text = m.get("text") or ""
                        partials.append(m)
                    elif t == "final":
                        finals.append(m)
                    elif t == "error":
                        errors.append(m)
                        return
                    elif t in ("verdict",):
                        finals.append(m)

            recv_task = asyncio.create_task(recv_loop())

            # 按真实节奏推流（150ms/包）
            sent = 0
            for i in range(0, len(pcm), CHUNK):
                await ws.send(pcm[i : i + CHUNK])
                sent += 1
                if sent % 20 == 0:
                    print(f"      ...已推 {sent} 包 / {len(partials)} partial")
                await asyncio.sleep(CHUNK_MS / 1000)

            print(f"[5] 推流完成 {sent} 包，发 end")
            await ws.send(json.dumps({"type": "end"}))

            # 等 final（云端闭包识别）
            deadline = time.time() + 25
            while time.time() < deadline and not finals and not errors:
                await asyncio.sleep(0.2)
            await asyncio.sleep(0.8)
            recv_task.cancel()
            try:
                await recv_task
            except (asyncio.CancelledError, Exception):
                pass

        elapsed = time.time() - t_start
        print()
        print("=" * 60)
        print(f"partial 帧数：{len(partials)}")
        if partials:
            last = partials[-1]
            print(f"  最后一个 partial：{json.dumps(last, ensure_ascii=False)[:600]}")
        print(f"final 帧数：{len(finals)}")
        for f in finals:
            print("  final:", json.dumps(f, ensure_ascii=False)[:900])
        print(f"error 帧数：{len(errors)}")
        for e in errors:
            print("  error:", json.dumps(e, ensure_ascii=False)[:400])
        if first_partial_ms is not None:
            print(f"首字延迟（本地观测）：{first_partial_ms:.0f}ms  首段文本={first_partial_text!r}")
        print(f"总耗时：{elapsed:.1f}s")

        # ---- 5. 查 asr_logs ----
        await asyncio.sleep(0.5)
        print()
        print("[6] 最近 asr_logs（本账号）：")
        r = await c.get("/api/auth/me", headers=_auth(token))
        print("     me:", json.dumps(r.json(), ensure_ascii=False)[:200])

        print()
        ok = bool(finals) and not errors
        print("结论：", "PASS ✅" if ok else "FAIL ❌")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
