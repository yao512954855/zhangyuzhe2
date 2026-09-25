"""
ASR 识别率预研脚本（离线工具，不进生产代码）

背景：见 docs/dev/05-ASR预研方案.md
本脚本用 TTS 合成"念读音频"替代真人录音，验证：
  1. 链路是否通（模型/格式/参数）
  2. 孤立字母序列能否被识别（核心疑点）
  3. 语种提示 en/zh 的差异
  4. 各类延迟基线

【重要局限】TTS 音频是标准发音，识别率会显著高于真实儿童口音。
本脚本产出的数字只能作为"上限估计"与"链路验证"，不能作为最终判分依据。

用法：
  DASHSCOPE_API_KEY=xxx python qa/asr_pilot.py [--quick]
输出：
  qa/asr_raw.csv    逐条明细
  qa/audio/*.wav    生成的音频（供多模态对照复用）
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

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
BASE = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
ROOT = pathlib.Path(__file__).resolve().parent
AUDIO = ROOT / "audio"
AUDIO.mkdir(exist_ok=True)

TTS_MODEL = "qwen3-tts-flash"
TTS_VOICE = "Cherry"
ASR_MODEL = "qwen3-asr-flash"

CALL_GAP = 0.25          # 调用间隔，避免触发限流
_http_calls = 0


def post(payload, timeout=90, retries=2):
    """统一 POST，返回 (ms, body)。失败重试以避开偶发限流。"""
    global _http_calls
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            BASE,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ms = round((time.time() - t0) * 1000)
                _http_calls += 1
                time.sleep(CALL_GAP)
                return ms, json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:400]
            last = (ms := round((time.time() - t0) * 1000)), {"HTTPError": e.code, "body": body}
            if e.code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return last
        except Exception as e:
            last = (round((time.time() - t0) * 1000)), {"ERR": str(e)[:300]}
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return last
    return last


def tts(text, voice=TTS_VOICE):
    """合成音频，返回 (url, ms)。"""
    ms, b = post({"model": TTS_MODEL,
                  "input": {"text": text, "voice": voice, "language_type": "English"}})
    if "HTTPError" in b or "ERR" in b:
        return None, ms, b
    try:
        return b["output"]["audio"]["url"], ms, None
    except Exception:
        return None, ms, b


def download(url, path):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            path.write_bytes(r.read())
        return True
    except Exception:
        return False


def asr_file(path, lang="en", hotwords=None):
    """本地音频 → 识别文本。返回 (text, ms, raw)。
    走 base64，避免 OSS URL 过期导致多模态对照时无法复用。"""
    b64 = base64.b64encode(path.read_bytes()).decode()
    payload = {
        "model": ASR_MODEL,
        "input": {"messages": [{"role": "user",
                                "content": [{"audio": f"data:audio/wav;base64,{b64}"}]}]},
        "parameters": {"asr_options": {"language": lang}},
    }
    if hotwords:
        payload["parameters"]["asr_options"]["vocabulary"] = hotwords
    ms, b = post(payload)
    if "HTTPError" in b or "ERR" in b:
        return None, ms, b
    try:
        txt = b["output"]["choices"][0]["message"]["content"][0]["text"]
        return txt, ms, b
    except Exception:
        return None, ms, b


# ---------- 归一化（与生产判分口径对齐，见需求 §6.2） ----------

def norm_text(s):
    """整词/句子归一化：小写、去标点、压空格。"""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_spell(s):
    """拼读归一化：抽取字母序列。
    返回 (字母串, 是否保留了字母分隔)。
    had_sep=False 说明引擎把它当单词连读处理了（并非真识别出字母序列）。"""
    if not s:
        return "", False
    # 形如 "C L A S S" 的序列，视为逐字母
    tokens = re.findall(r"\b[A-Za-z]\b", s)
    had_sep = len(tokens) >= 2
    if had_sep:
        return "".join(t.upper() for t in tokens), True
    # 否则退化为整串字母
    letters = re.sub(r"[^A-Za-z]", "", s).upper()
    return letters, False


# ---------- 测试集构造 ----------

def build_cases(words, quick=False):
    """产出 [(case_id, word, mode, tts_text, target, target_kind), ...]"""
    cases = []
    for w in words:
        word = w["word"]
        if quick and word not in ("class", "cat", "dictionary"):
            continue
        is_phrase = w["cat"] in ("短语", "句子")
        # 1) 整词/整句
        cases.append((f"{word}__whole", word, "whole", word, norm_text(word), "text"))
        # 2) 逐字母（短语和句子不拼）
        if not is_phrase and w["letters"]:
            spell_in = " ".join(w["letters"])          # C L A S S
            cases.append((f"{word}__spell", word, "spell", spell_in, w["letters"], "spell"))
        # 3) 音节切分
        if not is_phrase and w["syllables"]:
            syl = w["syllables"].replace("-", " ")     # cla ss
            cases.append((f"{word}__syllable", word, "syllable", syl, None, "none"))
    return cases


def main():
    if not KEY:
        print("ERROR: 请先设置 DASHSCOPE_API_KEY")
        sys.exit(1)

    quick = "--quick" in sys.argv
    spec = json.loads((ROOT / "pilot_words.json").read_text(encoding="utf-8"))
    cases = build_cases(spec["words"], quick)
    print(f"用例数: {len(cases)}  (quick={quick})")

    rows = []
    for i, (cid, word, mode, tts_text, target, kind) in enumerate(cases, 1):
        wav = AUDIO / f"{cid}.wav"
        # --- TTS ---
        if wav.exists():
            tts_ms = -1
        else:
            url, tts_ms, err = tts(tts_text)
            if url is None:
                rows.append(dict(case=cid, word=word, mode=mode, tts_input=tts_text,
                                 target=target, tts_ms=tts_ms, asr_ms="",
                                 asr_en="", norm="", correct_en="",
                                 had_sep="", asr_zh="", correct_zh="", note=f"TTS_FAIL {err}"))
                print(f"  [{i}/{len(cases)}] {cid:28} TTS FAIL")
                continue
            download(url, wav)

        # --- ASR: en ---
        txt_en, ms_en, err_en = asr_file(wav, lang="en")
        if txt_en is None:
            rows.append(dict(case=cid, word=word, mode=mode, tts_input=tts_text,
                             target=target, tts_ms=tts_ms, asr_ms=ms_en,
                             asr_en=f"<ERR {err_en}>", norm="", correct_en="",
                             had_sep="", asr_zh="", correct_zh="", note="ASR_EN_FAIL"))
            print(f"  [{i}/{len(cases)}] {cid:28} ASR(en) FAIL {err_en}")
            continue

        if kind == "spell":
            norm, had_sep = norm_spell(txt_en)
            correct_en = int(norm == target)
        elif kind == "text":
            norm, had_sep = norm_text(txt_en), ""
            correct_en = int(norm == target)
        else:  # syllable：无严格目标，人工判读
            norm, had_sep = norm_spell(txt_en)
            correct_en = ""

        # --- ASR: zh 对照组（仅 spell 模式，验证语种提示的影响）---
        asr_zh, correct_zh = "", ""
        if kind == "spell" and not quick:
            tz, _, _ = asr_file(wav, lang="zh")
            asr_zh = tz or ""
            nz, _ = norm_spell(asr_zh)
            correct_zh = int(nz == target)

        rows.append(dict(case=cid, word=word, mode=mode, tts_input=tts_text,
                         target=target or "", tts_ms=tts_ms, asr_ms=ms_en,
                         asr_en=txt_en, norm=norm, correct_en=correct_en,
                         had_sep=had_sep, asr_zh=asr_zh, correct_zh=correct_zh, note=""))
        flag = "OK " if correct_en == 1 else ("-- " if correct_en == "" else "MISS")
        print(f"  [{i}/{len(cases)}] {flag} {cid:28} -> {txt_en!r}")

    out = ROOT / "asr_raw.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n明细已写入 {out}  (共 {len(rows)} 行, HTTP {_http_calls} 次)")

    summarize(rows)


def summarize(rows):
    def rate(sub, key="correct_en"):
        vals = [r[key] for r in sub if isinstance(r[key], int)]
        if not vals:
            return None, 0
        return round(100 * sum(vals) / len(vals), 1), len(vals)

    print("\n===== 汇总（TTS 标准音，仅作上限参考）=====")
    for mode in ("whole", "spell", "syllable"):
        sub = [r for r in rows if r["mode"] == mode]
        pct, n = rate(sub)
        if pct is not None:
            print(f"  {mode:9} 准确率 {pct:5.1f}%  (n={n})")

    spell = [r for r in rows if r["mode"] == "spell"]
    if spell:
        sep = [r for r in spell if r["had_sep"] is True]
        print(f"  拼读：保留了字母分隔的占比 {len(sep)}/{len(spell)}"
              f" = {round(100*len(sep)/len(spell),1)}%")
        zh = [r["correct_zh"] for r in spell if isinstance(r["correct_zh"], int)]
        if zh:
            print(f"  拼读（误设 zh 语种）准确率 {round(100*sum(zh)/len(zh),1)}%  (n={len(zh)})")

    lat = [r["asr_ms"] for r in rows if isinstance(r["asr_ms"], int)]
    if lat:
        lat_s = sorted(lat)
        p50 = lat_s[len(lat_s) // 2]
        p95 = lat_s[min(len(lat_s) - 1, int(len(lat_s) * 0.95))]
        print(f"  ASR 延迟 P50 {p50}ms / P95 {p95}ms / max {lat_s[-1]}ms")

    empty = sum(1 for r in rows if not r["asr_en"])
    print(f"  空结果率 {round(100*empty/len(rows),1)}%  ({empty}/{len(rows)})")


if __name__ == "__main__":
    main()
