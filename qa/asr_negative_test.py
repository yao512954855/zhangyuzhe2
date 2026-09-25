# -*- coding: utf-8 -*-
"""负样本测试：故意念错的字母序列，看模型会不会「纠正」成正确单词。
这是判定「词化掩盖错误」风险的唯一手段。

流程：TTS 合成错误念法 -> omni 质检确认音频真实内容 -> 各模型识别 -> 三方对比
"""
import base64, csv, json, os, pathlib, re, time, urllib.request, urllib.error

KEY = os.environ["DASHSCOPE_API_KEY"]
BASE = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
NEG = pathlib.Path("qa/audio_neg")
NEG.mkdir(exist_ok=True)

# (tag, 目标单词, 实际念出的字母序列)  —— 中间故意错一位
NEG_CASES = [
    ("CLASS",     "CLASS",     "G L A S S"),        # 首字母错
    ("CAT",       "CAT",       "B A T"),            # 首字母错
    ("DOG",       "DOG",       "D O B"),            # 尾字母错
    ("BUS",       "BUS",       "B U Z"),            # 尾字母错
    ("APPLE",     "APPLE",     "A B P L E"),        # 中间错
    ("SCHOOL",    "SCHOOL",    "S C H O O I"),      # 尾字母错
]

ASR_MODELS = ["qwen3-asr-flash", "qwen-audio-3.0-asr-flash", "fun-asr-flash-2026-06-15"]


def post(payload, timeout=90, retries=2):
    for a in range(retries + 1):
        req = urllib.request.Request(BASE, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                time.sleep(0.25)
                return round((time.time() - t0) * 1000), json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < retries:
                time.sleep(1.5 * (a + 1)); continue
            return round((time.time() - t0) * 1000), {"_E": e.code}
        except Exception as e:
            if a < retries:
                time.sleep(1.5 * (a + 1)); continue
            return None, {"_ERR": str(e)[:120]}


def tts(text, voice="Cherry"):
    ms, b = post({"model": "qwen3-tts-flash",
                  "input": {"text": text, "voice": voice, "language_type": "English"}})
    if not isinstance(b, dict) or "output" not in b:
        return None
    return b["output"]["audio"]["url"]


def download(url, path):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as r:
        path.write_bytes(r.read())
    return path.stat().st_size


def asr(model, path, params=None):
    b64 = base64.b64encode(path.read_bytes()).decode()
    pl = {"model": model,
          "input": {"messages": [{"role": "user", "content": [{"audio": f"data:audio/wav;base64,{b64}"}]}]},
          "parameters": params or {"asr_options": {"language": "zh"}}}
    ms, b = post(pl)
    if not isinstance(b, dict):
        return None
    out = b.get("output") or {}
    inner = out.get("output") if isinstance(out.get("output"), dict) else None
    if inner and isinstance(inner.get("sentence"), dict):
        return inner["sentence"].get("text", "")
    ch = out.get("choices")
    if ch:
        c = ch[0].get("message", {}).get("content")
        if isinstance(c, list):
            return "".join(x.get("text", "") for x in c if isinstance(x, dict))
        if isinstance(c, str):
            return c
    return None


def omni_hear(path, prompt="这段音频里，说话人依次念出了哪些英文字母？只输出字母序列，用空格分隔，不要其他内容。"):
    b64 = base64.b64encode(path.read_bytes()).decode()
    ms, b = post({"model": "qwen3.5-omni-plus",
        "input": {"messages": [{"role": "user", "content": [
            {"audio": f"data:audio/wav;base64,{b64}"}, {"text": prompt}]}]},
        "parameters": {"modalities": ["text"]}})
    if not isinstance(b, dict):
        return None
    try:
        c = b["output"]["choices"][0]["message"]["content"]
        return "".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else str(c)
    except Exception:
        return None


def norm_spell(s):
    if not s:
        return ""
    L = re.findall(r"\b[A-Za-z]\b", s)
    if len(L) >= 2:
        return "".join(x.upper() for x in L)
    return re.sub(r"[^A-Za-z]", "", s).upper()


print("=" * 100)
print("负样本测试：音频实际念的是「错误的」字母序列，看模型是否忠实")
print("=" * 100)
rows = []
for tag, target, spoken in NEG_CASES:
    p = NEG / f"{tag}.wav"
    if not p.exists():
        url = tts(spoken)
        if not url:
            print(f"[{tag}] TTS 失败"); continue
        download(url, p)
        time.sleep(0.4)
    # 1) 质检：音频真实内容
    heard = omni_hear(p)
    h = norm_spell(heard)
    spoken_norm = spoken.replace(" ", "")
    audio_ok = (h == spoken_norm)
    # 2) 各模型识别
    res = {}
    for m in ASR_MODELS:
        params = {"format": "wav"} if m != "qwen3-asr-flash" else {"asr_options": {"language": "zh"}}
        r = asr(m, p, params)
        res[m] = norm_spell(r)
    print(f"\n[{tag}]  目标={target}  实际念出={spoken_norm}  omni听到={h} {'(质检OK)' if audio_ok else '⚠质检不通过'}")
    for m, got in res.items():
        if got == spoken_norm:
            verdict = "✓ 忠实(听到真实内容)"
        elif got == target:
            verdict = "🔴 危险：被纠正成正确词，错读被掩盖！"
        elif got == "":
            verdict = "× 空结果"
        else:
            verdict = f"~ 其他: {got}"
        print(f"     {m:30} -> {got!r:22} {verdict}")
        rows.append([tag, target, spoken_norm, h, audio_ok, m, got,
                     "faithful" if got == spoken_norm else ("masked" if got == target else "other")])

with open("qa/asr_negative_test.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["case", "target", "spoken_actual", "omni_heard", "audio_qc_ok", "model", "model_output", "verdict"])
    w.writerows(rows)

# 汇总
print("\n" + "=" * 100)
print("汇总（只看质检通过的样本）")
print(f"{'model':30}{'忠实':>8}{'掩盖(危险)':>12}{'其他':>8}")
for m in ASR_MODELS:
    sub = [r for r in rows if r[5] == m and r[4] == 1]
    if not sub:
        print(f"{m:30}{' 无':>8}"); continue
    fa = sum(1 for r in sub if r[7] == "faithful")
    mk = sum(1 for r in sub if r[7] == "masked")
    ot = len(sub) - fa - mk
    print(f"{m:30}{fa:>8}{mk:>12}{ot:>8}    (n={len(sub)})")
print("\n明细: qa/asr_negative_test.csv")
