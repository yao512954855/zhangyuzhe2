# -*- coding: utf-8 -*-
"""流式模型验证：① 负样本忠实度  ② 中文课文识别（语文场景）"""
import base64, json, os, pathlib, re, struct, threading, time, urllib.request, urllib.error, uuid
import numpy as np
import websocket

KEY = os.environ["DASHSCOPE_API_KEY"]
WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
HTTP = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
NEG = pathlib.Path("qa/audio_neg")
CN = pathlib.Path("qa/audio_cn")
CN.mkdir(exist_ok=True)

STREAM_MODELS = [
    "qwen-audio-3.1-asr-flash-streaming",
    "qwen-audio-3.0-asr-flash-streaming",
    "fun-asr-realtime",
]


def post(payload, timeout=90, retries=2):
    for a in range(retries + 1):
        req = urllib.request.Request(HTTP, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                time.sleep(0.2)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < retries:
                time.sleep(1.5 * (a + 1)); continue
            return None
        except Exception:
            if a < retries:
                time.sleep(1.5 * (a + 1)); continue
            return None


def read_wav_pcm16k(path, target_rate=16000):
    d = path.read_bytes()
    i, rate, ch, bits, data_off, data_len = 12, None, None, None, None, None
    while i + 8 <= len(d):
        cid, sz = d[i:i+4], struct.unpack('<I', d[i+4:i+8])[0]
        if cid == b'fmt ':
            ch = struct.unpack('<H', d[i+10:i+12])[0]
            rate = struct.unpack('<I', d[i+12:i+16])[0]
            bits = struct.unpack('<H', d[i+22:i+24])[0]
        if cid == b'data':
            data_off, data_len = i + 8, min(sz, len(d) - i - 8)
            break
        i += 8 + sz + (sz % 2)
    raw = d[data_off:data_off + data_len]
    arr = np.frombuffer(raw[:len(raw)//(bits//8)*(bits//8)], dtype=np.int16)
    if ch and ch > 1:
        arr = arr.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if rate != target_rate:
        n = int(len(arr) * target_rate / rate)
        arr = np.interp(np.linspace(0, len(arr)-1, n), np.arange(len(arr)), arr).astype(np.int16)
    return arr.tobytes(), round(len(arr)/target_rate, 2)


def stream_once(model, pcm, rate):
    tid = uuid.uuid4().hex[:32]
    chunk = int(rate * 0.1) * 2
    st = {"t0": None, "first": None, "texts": [], "events": [], "err": None, "done": False}

    def on_open(ws):
        ws.send(json.dumps({"header": {"action": "run-task", "task_id": tid, "streaming": "duplex"},
            "payload": {"task_group": "audio", "task": "asr", "function": "recognition",
                        "model": model, "parameters": {"sample_rate": rate, "format": "pcm"}, "input": {}}}))

    def on_message(ws, message):
        try:
            m = json.loads(message); ev = m["header"]["event"]; st["events"].append(ev)
            if ev == "task-started":
                st["t0"] = time.time()
                def push():
                    for o in range(0, len(pcm), chunk):
                        if ws.sock and ws.sock.connected:
                            ws.send(pcm[o:o+chunk], opcode=websocket.ABNF.OPCODE_BINARY)
                            time.sleep(0.1)
                    ws.send(json.dumps({"header": {"action": "finish-task", "task_id": tid, "streaming": "duplex"},
                                        "payload": {"input": {}}}))
                threading.Thread(target=push, daemon=True).start()
            elif ev == "result-generated":
                t = (m.get("payload", {}).get("output", {}).get("sentence", {}) or {}).get("text", "")
                if t:
                    if st["first"] is None:
                        st["first"] = round((time.time() - (st["t0"] or time.time()))*1000)
                    st["texts"].append(t)
            elif ev in ("task-finished", "task-failed"):
                if ev == "task-failed":
                    st["err"] = m.get("payload", {}).get("output", {}).get("message")
                st["done"] = True
                ws.close()
        except Exception as e:
            st["events"].append(f"err:{e}")

    app = websocket.WebSocketApp(WS_URL, header={"Authorization": f"Bearer {KEY}"},
        on_open=on_open, on_message=on_message,
        on_error=lambda w, e: st.update(err=str(e)[:150]),
        on_close=lambda *a: None)
    th = threading.Thread(target=lambda: app.run_forever(ping_timeout=5), daemon=True)
    th.start()
    th.join(timeout=len(pcm)/(rate*2) + 10)
    try: app.close()
    except Exception: pass
    return st


def norm_spell(s):
    if not s:
        return ""
    L = re.findall(r"\b[A-Za-z]\b", s)
    if len(L) >= 2:
        return "".join(x.upper() for x in L)
    return re.sub(r"[^A-Za-z]", "", s).upper()

# ========== ① 流式负样本忠实度 ==========
print("=" * 96)
print("① 流式模型 · 负样本忠实度（音频念的是错字母，看是否被纠正）")
print("=" * 96)
NEG_CASES = [("CLASS", "GLASS"), ("APPLE", "ABPLE"), ("CAT", "BAT")]
for tag, spoken in NEG_CASES:
    p = NEG / f"{tag}.wav"
    if not p.exists():
        print(f"  {tag}: 音频缺失"); continue
    pcm, dur = read_wav_pcm16k(p)
    print(f"\n[{tag}]  目标={tag}  实际念出={spoken}")
    for m in STREAM_MODELS:
        st = stream_once(m, pcm, 16000)
        if not st["texts"]:
            print(f"     {m:34} 无结果 events={','.join(dict.fromkeys(st['events']))} err={st['err']}")
            continue
        got = norm_spell(st["texts"][-1])
        v = "✓忠实" if got == spoken else ("🔴被纠正(掩盖)" if got == tag else f"~{got}")
        print(f"     {m:34} -> {st['texts'][-1]!r:24} {v}  首字={st['first']}ms")

# ========== ② 中文课文识别 ==========
print("\n" + "=" * 96)
print("② 中文课文识别（语文背诵场景）")
print("=" * 96)
TEXTS = [
    ("jinYesi", "床前明月光，疑是地上霜。举头望明月，低头思故乡。"),
    ("beijing", "我与父亲不相见已二年余了，我最不能忘记的是他的背影。"),
]
for tag, text in TEXTS:
    p = CN / f"{tag}.wav"
    if not p.exists():
        b = post({"model": "qwen3-tts-flash",
                  "input": {"text": text, "voice": "Cherry", "language_type": "Chinese"}})
        if not b or "output" not in b:
            print(f"  {tag} TTS 失败"); continue
        url = b["output"]["audio"]["url"]
        with urllib.request.urlopen(url, timeout=60) as r:
            p.write_bytes(r.read())
        time.sleep(0.4)
    pcm, dur = read_wav_pcm16k(p)
    print(f"\n[{tag}] 原文: {text}")
    print(f"        音频时长 {dur}s")
    for m in STREAM_MODELS:
        st = stream_once(m, pcm, 16000)
        final = st["texts"][-1] if st["texts"] else "<无>"
        print(f"     {m:34} 首字={st['first']}ms  中间包={len(st['texts'])}")
        print(f"        -> {final!r}")
