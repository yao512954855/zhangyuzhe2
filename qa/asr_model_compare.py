# -*- coding: utf-8 -*-
"""非流式 ASR 候选横向对比。
输入：qa/audio/ 现有音频（TTS 合成）
质检：沿用 qa/_omni_check.tsv 的 omni 结论，只在干净音频上统计
输出：qa/asr_model_compare.csv
"""
import base64, csv, json, os, pathlib, re, time, urllib.request, urllib.error

KEY = os.environ["DASHSCOPE_API_KEY"]
AUD = pathlib.Path("qa/audio")
URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

MODELS = [
    ("qwen3-asr-flash",            {}),                      # 上一轮基线
    ("qwen-audio-3.0-asr-flash",   {"format": "wav"}),       # 新，带时间戳
    ("fun-asr-flash-2026-06-15",   {"format": "wav"}),       # 反向流式候选
]


def post(payload, timeout=90, retries=2):
    for a in range(retries + 1):
        req = urllib.request.Request(
            URL, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return round((time.time() - t0) * 1000), json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < retries:
                time.sleep(1.5 * (a + 1)); continue
            return round((time.time() - t0) * 1000), {"_E": e.code}
        except Exception as e:
            if a < retries:
                time.sleep(1.5 * (a + 1)); continue
            return None, {"_ERR": str(e)[:120]}


def parse(body):
    """→ (text, has_timestamps, n_words)"""
    out = body.get("output") or {}
    # 结构1: qwen-audio-3.x → output.output.sentence
    inner = out.get("output") if isinstance(out.get("output"), dict) else None
    if inner and isinstance(inner.get("sentence"), dict):
        s = inner["sentence"]
        return s.get("text", ""), bool(s.get("words")), len(s.get("words") or [])
    # 结构2: qwen3-asr-flash → output.choices[0].message.content
    ch = out.get("choices")
    if ch:
        c = ch[0].get("message", {}).get("content")
        if isinstance(c, list):
            return "".join(x.get("text", "") for x in c if isinstance(x, dict)), False, 0
        if isinstance(c, str):
            return c, False, 0
    return None, False, 0


def norm_spell(s):
    """字母序列归一化：优先取孤立单字母"""
    if not s:
        return ""
    letters = re.findall(r"\b[A-Za-z]\b", s)
    if len(letters) >= 2:
        return "".join(x.upper() for x in letters)
    return re.sub(r"[^A-Za-z]", "", s).upper()


def norm_text(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------- 读质检结论 ----------
clean = {}   # case -> target
for line in pathlib.Path("qa/_omni_check.tsv").read_text(encoding="utf-8").strip().split("\n"):
    f = line.split("\t")
    if len(f) >= 7 and f[5] == "1":          # omni_correct == 1
        clean[f[0]] = f[2]

print(f"omni 认定干净且目标为字母的样本: {len(clean)} 条")
print("=" * 92)
print(f"{'case':24}{'target':>10}  " + "".join(f"{m:26}" for m, _ in MODELS))
print("=" * 92)

rows = []
stats = {m: {"ok": 0, "n": 0, "lat": []} for m, _ in MODELS}
for case, target in sorted(clean.items()):
    p = AUD / f"{case}.wav"
    if not p.exists():
        continue
    b64 = base64.b64encode(p.read_bytes()).decode()
    line = f"{case:24}{target:>10}  "
    for model, extra in MODELS:
        params = {"asr_options": {"language": "zh"}}
        params.update(extra)
        payload = {"model": model,
                   "input": {"messages": [{"role": "user", "content": [{"audio": f"data:audio/wav;base64,{b64}"}]}]},
                   "parameters": params}
        ms, body = post(payload)
        if ms is None:
            line += f"{'<网络错误>':26}"; continue
        txt, has_ts, nw = parse(body)
        if txt is None:
            line += f"{('ERR:' + str(body.get('_E') or '?')):26}"; continue
        got = norm_spell(txt)
        ok = got == target
        stats[model]["n"] += 1
        stats[model]["lat"].append(ms)
        if ok:
            stats[model]["ok"] += 1
        mark = "✓" if ok else "×"
        line += f"{(mark + repr(txt)[:22] + ('|ts%d' % nw if has_ts else '')):26}"
        rows.append([case, target, model, txt, got, int(ok), ms, nw])
    print(line)

print("=" * 92)
for model, _ in MODELS:
    s = stats[model]
    if not s["n"]:
        print(f"{model:30} 无有效样本"); continue
    lat = sorted(s["lat"])
    p50 = lat[len(lat)//2]
    p95 = lat[min(len(lat)-1, int(len(lat)*0.95))]
    print(f"{model:30} 准确率 {s['ok']}/{s['n']} = {round(100*s['ok']/s['n'],1):>5}%   "
          f"延迟 P50 {p50}ms  P95 {p95}ms")

with open("qa/asr_model_compare.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["case", "target", "model", "raw_text", "normalized", "correct", "latency_ms", "n_words_ts"])
    w.writerows(rows)
print("\n明细已写入 qa/asr_model_compare.csv")
