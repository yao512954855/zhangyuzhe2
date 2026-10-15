# -*- coding: utf-8 -*-
"""OCR 本地化手写门（docs/localize-dev/01 §7.1 / 12 §4）。

收集 qa 目录手写样张（qa/*.png + qa/**/*.png），对每张跑本地 RapidOCR
（LocalOcrClient）+ 现有解析对齐链路（dictation_service._parse_blocks / _align），
按 grade_handwriting 的 results 口径算逐词正确率，输出每张明细 + 汇总结论。

门标准（12 §4）：每张逐词判分正确率 ≥ 80% 且非「全部误判」；通过张数 / 总张 ≥ 80%
即门通过，否则停下上报实测明细，等姚工裁决。

⚠️ 样张不足时（<10 张或全是印刷体/截图）**如实上报「样张不足，门未判定」**，
绝不用印刷体/截图充数。
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from app.services.ocr_local import LocalOcrClient  # noqa: E402
from app.services import dictation_service  # noqa: E402

# 门必须由本地引擎判定：强制 local 模式，不走云端（契约 §1.2）
os.environ["CAP_OCR"] = "local"

_QA_DIR = os.path.join(BACKEND, "..", "qa")
_GATE_PASS_RATIO = 0.8  # 通过张数占比阈值（≈8/10）


def collect_samples() -> list[str]:
    """收集 qa 下所有 png（含子目录）。"""
    patterns = [os.path.join(_QA_DIR, "*.png"), os.path.join(_QA_DIR, "**", "*.png")]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat, recursive=True))
    # 去重 + 稳定排序
    return sorted(set(os.path.abspath(f) for f in files))


def load_manifest() -> dict[str, list[str]]:
    """可选真值清单 qa/handwriting_manifest.json：{文件名: [期望单词,...]}。"""
    path = os.path.join(_QA_DIR, "handwriting_manifest.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    return {}


def run_gate(samples: list[str], manifest: dict[str, list[str]]) -> int:
    """跑门：返回退出码（0=通过，1=未通过，2=样张不足未判定）。

    手写样张的判定依据 = 在真值清单 handwriting_manifest.json 中（文件名 → 期望单词）。
    无手写样张 / 不足 10 张 → 如实上报「样张不足，门未判定」，绝不用截图充数。
    """
    # 仅把清单里有真值的图当作手写样张
    handwriting = [s for s in samples if os.path.basename(s) in manifest]
    if len(handwriting) < 10:
        print("\n" + "=" * 60)
        print(f"⚠️  手写样张不足：真值清单 handwriting_manifest.json 仅覆盖 "
              f"{len(handwriting)} 张（手写门要求 ≥ 10 张手写样张）。")
        print(f"    目录下共找到 {len(samples)} 张 png，但多为菜单/App截图/结算页，"
              f"非手写答题图。")
        print("    门未判定 —— 请姚工补拍 ≥10 张手写样张（每张 3~6 个单词），")
        print("    并写入 qa/handwriting_manifest.json（文件名 → [期望单词列表]）。")
        print("    严禁用印刷体/截图充数。")
        return 2

    client = LocalOcrClient()
    passed = 0
    any_all_wrong = False
    print(f"\n===== OCR 本地化手写门：{len(handwriting)} 张手写样张 =====")
    for idx, path in enumerate(handwriting, 1):
        with open(path, "rb") as f:
            image_bytes = f.read()
        name = os.path.basename(path)
        expected = manifest[name]
        t0 = time.monotonic()
        try:
            result = dictation_service.grade_handwriting(image_bytes, expected)
        except Exception as exc:  # noqa: BLE001
            print(f"[{idx}/{len(handwriting)}] {name}: 识别异常 {type(exc).__name__}: {exc}")
            continue
        cost = (time.monotonic() - t0) * 1000
        results = result["results"]
        correct = sum(1 for r in results if r["correct"])
        total = len(results)
        acc = correct / total if total else 0.0
        image_ok = acc >= _GATE_PASS_RATIO and correct > 0
        if correct == 0:
            any_all_wrong = True
        passed += 1 if image_ok else 0
        print(f"\n[{idx}/{len(handwriting)}] {name}  ({cost:.0f}ms, 正确 {correct}/{total}, "
              f"正确率 {acc*100:.0f}%)  {'✓通过' if image_ok else '✗未过'}")
        for r in results:
            mark = "✓" if r["correct"] else "✗"
            print(f"    {mark} {r['word']:<10} -> 识别「{r['recognized']}」"
                  f" conf={r['confidence']}" + (" (拿不准)" if r["uncertain"] else ""))

    print("\n" + "=" * 60)
    print(f"汇总：通过 {passed}/{len(handwriting)} 张")
    if any_all_wrong:
        print("存在「全部误判」的样张 → 门未通过。")
        return 1
    if passed / len(handwriting) >= _GATE_PASS_RATIO:
        print("✅ 门通过（通过张数占比 ≥ 80%）。")
        return 0
    print("❌ 门未通过（通过张数占比 < 80%），停下上报实测明细，等姚工裁决。")
    return 1


def diagnostic(samples: list[str]) -> None:
    """诊断：演示本地引擎可用 + 单张识别耗时（不计入门判定）。

    用目录下所有 png（多为截图）跑一遍，证明引擎能加载并出块即可。
    """
    if not samples:
        return
    print("\n----- 诊断（不计入门判定）：本地引擎加载 + 单张识别耗时 -----")
    client = LocalOcrClient()
    first = True
    for path in samples:
        with open(path, "rb") as f:
            image_bytes = f.read()
        t0 = time.monotonic()
        try:
            raw = client.recognize(image_bytes)
            cost = (time.monotonic() - t0) * 1000
            if first:
                first = False
                print(f"  （首张含模型加载耗时；后续为纯推理）")
            data = json.loads(raw["Data"])
            words = [b.get("word") for b in data.get("prism_wordsInfo", [])]
            print(f"  {os.path.basename(path)}: {cost:.0f}ms 识别块数={len(words)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {os.path.basename(path)}: 异常 {type(exc).__name__}: {exc}")


def main() -> int:
    samples = collect_samples()
    manifest = load_manifest()
    print(f"找到 png 样张 {len(samples)} 张（手写门需 ≥10 张真值手写样张）。")
    code = run_gate(samples, manifest)
    if code == 2:
        diagnostic(samples)
    return code


if __name__ == "__main__":
    sys.exit(main())
