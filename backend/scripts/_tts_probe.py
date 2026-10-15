# -*- coding: utf-8 -*-
"""qwen-tts 可用性探针（复用 ASR 的百炼 key，直连不走代理）。"""
import asyncio
import json
import sys
import time

import httpx
import yaml

def main() -> None:
    cfg = yaml.safe_load(open("asr.yaml", encoding="utf-8"))
    key = cfg["aliyun"]["api_key"]
    url = cfg["aliyun"]["http_endpoint"]
    text = sys.argv[1] if len(sys.argv) > 1 else "apple"
    t0 = time.time()
    r = httpx.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": "qwen-tts",
            "input": {"text": text},
            "voice": "Cherry",
        },
        timeout=30,
        trust_env=False,
    )
    dt = time.time() - t0
    print(f"HTTP {r.status_code} in {dt:.2f}s")
    data = r.json()
    print(json.dumps(data, ensure_ascii=False, indent=1)[:600])
    if r.status_code == 200:
        audio = data.get("output", {}).get("audio", {})
        aurl = audio.get("url")
        if aurl:
            t1 = time.time()
            a = httpx.get(aurl, timeout=30, trust_env=False)
            print(f"audio GET {a.status_code} in {time.time()-t1:.2f}s bytes={len(a.content)} head={a.content[:4]!r}")
            open("../qa/audio/_tts_probe.mp3", "wb").write(a.content)
            print("saved ../qa/audio/_tts_probe.mp3")

main()
