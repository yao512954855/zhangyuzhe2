# -*- coding: utf-8 -*-
"""流式 ASR 探测：可用性 + 首字延迟。
协议依据：阿里云百炼「实时语音识别」文档
  run-task(action) -> 二进制音频帧 -> finish-task(action)
  事件: task-started / result-generated / task-finished / task-failed
"""
import json, os, pathlib, struct, time, uuid
import numpy as np
import websocket

KEY = os.environ["DASHSCOPE_API_KEY"]
WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
AUD = pathlib.Path("qa/audio")

MODELS = [
    "qwen-audio-3.1-asr-flash-streaming",
    "qwen-audio-3.0-asr-flash-streaming",
    "fun-asr-realtime",
    "qwen3-asr-flash-realtime",
]

CASES = ["class__spell", "cat__spell", "apple__spell"]   # 短/中/长


def read_wav_pcm16k(path, target_rate=16000):
    """读 wav 并重采样到 target_rate，返回 16bit 单声道 PCM bytes 和时长。"""
    d = path.read_bytes()
    # 解析 fmt / data
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
    if None in (rate, ch, bits):
        return None, 0
    arr = np.frombuffer(raw[:len(raw) // (bits // 8) * (bits // 8)], dtype=np.int16)
    if ch and ch > 1:
        arr = arr.reshape(-1, ch).mean(axis=1).astype(np.int16)
    dur = len(arr) / rate
    if rate != target_rate:
        n_out = int(len(arr) * target_rate / rate)
        xi = np.linspace(0, len(arr) - 1, n_out)
        arr = np.interp(xi, np.arange(len(arr)), arr).astype(np.int16)
    return arr.tobytes(), round(dur, 3)


def run_one(model, pcm, rate, chunk_ms=100, label=""):
    """返回 (status, first_delay_ms, final_text, n_intermediate, raw_events)"""
    tid = uuid.uuid4().hex[:32]
    chunk = int(rate * chunk_ms / 1000) * 2
    st = {"t_start": None, "first": None, "texts": [], "events": [], "err": None, "opened": False}

    def on_open(ws):
        st["opened"] = True
        ws.send(json.dumps({
            "header": {"action": "run-task", "task_id": tid, "streaming": "duplex"},
            "payload": {"task_group": "audio", "task": "asr", "function": "recognition",
                        "model": model,
                        "parameters": {"sample_rate": rate, "format": "pcm"},
                        "input": {}}}))

    def on_message(ws, message):
        ev = None
        try:
            m = json.loads(message)
            ev = m["header"]["event"]
            st["events"].append(ev)
            if ev == "task-started":
                st["t_start"] = time.time()

                def push():
                    for o in range(0, len(pcm), chunk):
                        if ws.sock and ws.sock.connected:
                            ws.send(pcm[o:o+chunk], opcode=websocket.ABNF.OPCODE_BINARY)
                            time.sleep(chunk_ms / 1000.0)
                    ws.send(json.dumps({"header": {"action": "finish-task", "task_id": tid, "streaming": "duplex"},
                                        "payload": {"input": {}}}))
                import threading
                threading.Thread(target=push, daemon=True).start()
            elif ev == "result-generated":
                txt = (m.get("payload", {}).get("output", {}).get("sentence", {}) or {}).get("text", "")
                if txt:
                    if st["first"] is None:
                        st["first"] = round((time.time() - (st["t_start"] or time.time())) * 1000)
                    st["texts"].append(txt)
            elif ev in ("task-finished", "task-failed"):
                st["err"] = m.get("payload", {}).get("output", {}).get("message") if ev == "task-failed" else None
                ws.close()
        except Exception as e:
            st["events"].append(f"parse_err:{e}")

    def on_error(ws, err):
        st["err"] = str(err)[:200]

    def on_close(ws, *a):
        pass

    app = websocket.WebSocketApp(WS_URL, header={"Authorization": f"Bearer {KEY}"},
                                 on_open=on_open, on_message=on_message,
                                 on_error=on_error, on_close=on_close)
    t0 = time.time()
    import threading
    th = threading.Thread(target=lambda: app.run_forever(ping_timeout=5), daemon=True)
    th.start()
    # 音频时长 + 余量，等待结束
    timeout = len(pcm) / (rate * 2) + 8
    th.join(timeout=timeout)
    try:
        app.close()
    except Exception:
        pass

    if not st["events"]:
        return "无响应", None, None, 0, st
    if "task-failed" in st["events"]:
        return "task-failed", None, None, 0, st
    if not st["texts"]:
        return "无识别结果", None, None, 0, st
    return "OK", st["first"], st["texts"][-1], len(st["texts"]), st


for model in MODELS:
    print("=" * 88)
    print(f"模型: {model}")
    for case in CASES:
        p = AUD / f"{case}.wav"
        if not p.exists():
            continue
        pcm, dur = read_wav_pcm16k(p)
        if pcm is None:
            print(f"  {case:20} 音频解析失败"); continue
        status, first, final, n, st = run_one(model, pcm, 16000)
        evs = ",".join(dict.fromkeys(st["events"]))
        print(f"  {case:20} 时长{dur:>5}s  {status:12} 首字={first}ms  中间结果={n}  最终={final!r}")
        if status != "OK" or st["err"]:
            print(f"      事件: {evs}")
            if st["err"]:
                print(f"      错误: {st['err']}")
