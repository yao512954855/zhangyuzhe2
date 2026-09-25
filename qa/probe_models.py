# -*- coding: utf-8 -*-
"""探测候选 ASR 模型的可用性与接口形态。
只跑 1 条音频（class__spell.wav，omni 质检确认为干净音频）。
"""
import base64, json, os, pathlib, time, urllib.request, urllib.error

KEY = os.environ["DASHSCOPE_API_KEY"]
AUDIO = pathlib.Path("qa/audio/class__spell.wav")
B64 = base64.b64encode(AUDIO.read_bytes()).decode()
AUDIO_URL = f"data:audio/wav;base64,{B64}"

ENDPOINTS = {
    "mmgen": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    "chat": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
}

CANDIDATES = [
    "qwen-audio-3.0-asr-flash",
    "qwen-audio-3.0-realtime-flash",
    "qwen-audio-3.0-realtime-plus",
    "qwen-audio-3.1-realtime-plus",
    "qwen-audio-3.1-asr",
    "qwen-audio-3.1-asr-message",
    "qwen3-asr-flash-2026-02-10",
    "fun-asr-flash-2026-06-15",
]


def post(url, payload, timeout=90, retries=1):
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return round((time.time() - t0) * 1000), json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:260]
            if e.code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(1.5 * (attempt + 1)); continue
            return round((time.time() - t0) * 1000), {"_HTTPError": e.code, "_body": body}
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1)); continue
            return round((time.time() - t0) * 1000), {"_ERR": str(e)[:200]}


def extract_text(body):
    """尽可能从多种响应结构里抠出文本。"""
    try:
        out = body.get("output", {})
        # 结构A: multimodal-generation
        ch = out.get("choices")
        if ch:
            msg = ch[0].get("message", {})
            c = msg.get("content")
            if isinstance(c, list):
                return "".join(x.get("text", "") for x in c if isinstance(x, dict))
            if isinstance(c, str):
                return c
        # 结构B: OpenAI 兼容
        ch2 = body.get("choices")
        if ch2:
            return ch2[0].get("message", {}).get("content", "")
        # 结构C: 直接 text 字段
        if isinstance(out.get("text"), str):
            return out["text"]
    except Exception:
        pass
    return None


print(f"音频: {AUDIO.name}  size={AUDIO.stat().st_size}B")
print("=" * 78)
for model in CANDIDATES:
    for ep_name, url in ENDPOINTS.items():
        if ep_name == "mmgen":
            payload = {"model": model,
                       "input": {"messages": [{"role": "user", "content": [{"audio": AUDIO_URL}]}]},
                       "parameters": {"asr_options": {"language": "zh"}}}
        else:
            payload = {"model": model,
                       "messages": [{"role": "user", "content": [
                           {"type": "input_audio", "input_audio": {"data": AUDIO_URL.split(",", 1)[1], "format": "wav"}}]}],
                       "modalities": ["text"]}
        ms, body = post(url, payload)
        txt = extract_text(body)
        if txt is not None:
            print(f"[OK ] {model:32} via {ep_name:5} {ms:>6}ms  -> {txt!r}")
        else:
            err = body.get("_HTTPError") or body.get("_ERR") or body.get("code") or ""
            detail = str(body.get("_body") or body.get("message") or "")[:110]
            print(f"[-- ] {model:32} via {ep_name:5} {ms:>6}ms  {err} {detail}")
    print("-" * 78)
