"""
ASR 预研附加测试（离线工具）

两个子测试，对应预研方案 §5.3 与 §6：
  1. --stability  同一段音频重复识别 N 次，检查结果稳定性
  2. --multimodal 对拼读音频跑多模态模型，问"念了哪些字母"，与专用 ASR 对照

用法：
  python qa/asr_extra.py --stability 3
  python qa/asr_extra.py --multimodal
"""
import base64
import csv
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
BASE = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
ROOT = pathlib.Path(__file__).resolve().parent
AUDIO = ROOT / "audio"

ASR_MODEL = "qwen3-asr-flash"
MM_MODELS = ["qwen3.5-omni-plus", "qwen3-omni-flash", "qwen-audio-3.0-asr-flash"]
PROMPT = ("这段音频里，说话人依次念出了哪些英文字母？"
          "只输出字母序列，用空格分隔（例如 C L A S S），不要任何其他内容。")

GAP = 0.3


def post(payload, timeout=90, retries=2):
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            BASE, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ms = round((time.time() - t0) * 1000)
                time.sleep(GAP)
                return ms, json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = (round((time.time() - t0) * 1000),
                    {"HTTPError": e.code, "body": e.read().decode()[:300]})
            if e.code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return last
        except Exception as e:
            last = (round((time.time() - t0) * 1000), {"ERR": str(e)[:200]})
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return last
    return last


def b64_of(p: pathlib.Path):
    return base64.b64encode(p.read_bytes()).decode()


def asr_once(p, lang="en"):
    ms, b = post({"model": ASR_MODEL,
                  "input": {"messages": [{"role": "user",
                                          "content": [{"audio": f"data:audio/wav;base64,{b64_of(p)}"}]}]},
                  "parameters": {"asr_options": {"language": lang}}})
    if "HTTPError" in b or "ERR" in b:
        return None, ms, b
    try:
        return b["output"]["choices"][0]["message"]["content"][0]["text"], ms, b
    except Exception:
        return None, ms, b


def mm_once(model, p):
    ms, b = post({"model": model,
                  "input": {"messages": [{"role": "user", "content": [
                      {"audio": f"data:audio/wav;base64,{b64_of(p)}"},
                      {"text": PROMPT}]}]},
                  "parameters": {"modalities": ["text"]}})
    if "HTTPError" in b or "ERR" in b:
        return None, ms, b
    try:
        content = b["output"]["choices"][0]["message"]["content"]
        if isinstance(content, list):
            txt = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        else:
            txt = str(content)
        return txt, ms, b
    except Exception:
        return None, ms, b


def norm_spell(s):
    if not s:
        return ""
    toks = re.findall(r"\b[A-Za-z]\b", s)
    if len(toks) >= 2:
        return "".join(t.upper() for t in toks)
    return re.sub(r"[^A-Za-z]", "", s).upper()


def load_rows():
    f = ROOT / "asr_raw.csv"
    with open(f, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def test_stability(n):
    print(f"===== 稳定性测试（同一音频重复 {n} 次）=====")
    rows = [r for r in load_rows() if r["mode"] in ("whole", "spell")]
    picks = rows[:8]
    unstable = 0
    for r in picks:
        wav = AUDIO / f"{r['case']}.wav"
        if not wav.exists():
            continue
        outs = []
        for _ in range(n):
            t, _, _ = asr_once(wav, lang="en")
            outs.append(t)
        c = Counter(outs)
        stable = len(c) == 1
        if not stable:
            unstable += 1
        print(f"  {r['case']:28} 唯一结果 {len(c)}/{n}  {'稳定' if stable else '★不稳定'}")
        for k, v in c.most_common():
            print(f"        x{v}  {k!r}")
    print(f"\n  不稳定样本: {unstable}/{len(picks)}  "
          f"({round(100*unstable/max(1,len(picks)),1)}%)")


def test_multimodal():
    rows = [r for r in load_rows() if r["mode"] == "spell"]
    print(f"===== 多模态对照（{len(rows)} 条拼读音频）=====")
    out_rows = []
    for model in MM_MODELS:
        print(f"\n--- {model} ---")
        ok = n_valid = 0
        lat = []
        for r in rows:
            wav = AUDIO / f"{r['case']}.wav"
            if not wav.exists():
                continue
            txt, ms, raw = mm_once(model, wav)
            if txt is None:
                print(f"  {r['case']:28} ⚠ {str(raw)[:120]}")
                out_rows.append({"model": model, "case": r["case"], "target": r["target"],
                                 "mm_text": "", "norm": "", "correct": "", "ms": ms})
                continue
            got = norm_spell(txt)
            hit = int(got == r["target"])
            ok += hit
            n_valid += 1
            lat.append(ms)
            out_rows.append({"model": model, "case": r["case"], "target": r["target"],
                             "mm_text": txt, "norm": got, "correct": hit, "ms": ms})
            print(f"  {'OK ' if hit else 'MISS'} {r['case']:28} target={r['target']:12} "
                  f"-> {txt!r}")
        if n_valid:
            lat.sort()
            print(f"  准确率 {round(100*ok/n_valid,1)}%  (n={n_valid})  "
                  f"P50 {lat[len(lat)//2]}ms")
    f = ROOT / "asr_multimodal.csv"
    with open(f, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["model", "case", "target", "mm_text", "norm", "correct", "ms"])
        w.writeheader()
        w.writerows(out_rows)
    print(f"\n明细: {f}")


if __name__ == "__main__":
    if not KEY:
        print("ERROR: 请先设置 DASHSCOPE_API_KEY"); sys.exit(1)
    if "--stability" in sys.argv:
        n = int(sys.argv[sys.argv.index("--stability") + 1])
        test_stability(n)
    elif "--multimodal" in sys.argv:
        test_multimodal()
    else:
        print(__doc__)
