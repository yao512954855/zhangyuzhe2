"""对齐口径自检：把**完全正确的课文文本**直接 feed 给 ReciteService，
看 `matched_chars` 是否等于 `total_chars`（排除 ASR 因素，只看对齐算法）。

跑法：`env -u PYTHONPATH .venv/Scripts/python.exe scripts/_cn_feed_check.py`
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys

import httpx

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

BASE = "http://127.0.0.1:8810"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        uname = f"A{random.randint(10_000_000, 99_999_999)}"
        r = await c.post(
            "/api/auth/register",
            json={"username": uname, "real_name": "对齐自检", "password": "test1234"},
        )
        token = r.json()["data"]["access_token"]

        r = await c.post(
            "/api/sessions",
            json={"unit_id": 3, "content_type": "chinese"},
            headers=_auth(token),
        )
        sid = r.json()["data"]["session_id"]

        cases = {
            "带标点（ASR 原样）": "我与父亲不相见已二年余了，我最不能忘记的是他的背影。那年冬天，祖母死了，父亲的差使也交卸了，正是祸不单行的日子。",
            "无标点（content_plain）": "我与父亲不相见已二年余了我最不能忘记的是他的背影那年冬天祖母死了父亲的差使也交卸了正是祸不单行的日子",
        }
        for label, text in cases.items():
            r = await c.post(
                "/api/recitations",
                json={"session_id": sid, "text_id": 1, "scope": [0, 1]},
                headers=_auth(token),
            )
            rid = r.json()["data"]["recitation_id"]
            r = await c.post(
                f"/api/recitations/{rid}/feed",
                json={"partial_text": text, "silence_ms": 0},
                headers=_auth(token),
            )
            j = r.json()
            data = j.get("data") or {}
            prog = data.get("progress") or data.get("recite") or data
            print(f"[{label}] len(text)={len(text)} code={j.get('code')}")
            print("   ", json.dumps(prog, ensure_ascii=False)[:600])

        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
