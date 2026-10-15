"""本地 TTS 探针门 —— 契约《docs/localize-dev/01-契约冻结》§7.3。

对 4 条文案各合成一次，落 qa/audio/_tts_local_<case>.wav，
stdout 打印 bytes 与 RIFF 头检查结果；4 条全过（非空 + RIFF 头）才算门通过。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/_tts_local_probe.py

依赖：sherpa-onnx 已装 + 两 TTS 模型已下载（见 scripts/download_models.py）。
任一文案失败会在 stdout 标红（这里用文字），并以退出码 1 结束。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "qa" / "audio"

# 让脚本可直接 `python scripts/_tts_local_probe.py` 运行（无需 pytest 注入路径）
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

CASES = [
    ("apple", "en-US"),
    ("good morning", "en-US"),
    ("苹果", "zh-CN"),
    ("你好吗", "zh-CN"),
]


def main() -> int:
    from app.services.tts_local import synthesize_local, is_ready

    ready, detail = is_ready()
    print(f"[probe] is_ready={ready} detail={detail}")
    if not ready:
        print("[probe] 本地 TTS 未就绪，探针门无法跑全：", detail)
        # 仍尝试合成（打印每条实际结果），便于定位缺哪个模型
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_ok = True
    for text, lang in CASES:
        case = f"{lang}__{text}".replace(" ", "_")
        out_path = OUT_DIR / f"_tts_local_{case}.wav"
        try:
            t0 = time.monotonic()
            audio = synthesize_local(text, lang)
            cost = time.monotonic() - t0
            is_riff = audio.startswith(b"RIFF")
            ok = is_riff and len(audio) > 1024
            out_path.write_bytes(audio)
            status = "PASS" if ok else "FAIL"
            print(
                f"[{status}] {lang!r} {text!r} bytes={len(audio)} "
                f"riff={is_riff} cost={cost*1000:.0f}ms -> {out_path.name}"
            )
            all_ok = all_ok and ok
        except Exception as exc:  # noqa: BLE001 - 探针如实记录
            all_ok = False
            print(f"[FAIL] {lang!r} {text!r} 合成异常: {type(exc).__name__}: {exc}")

    print("=" * 60)
    if all_ok:
        print("[probe] TTS 门通过：4 条 wav 全部非空且 RIFF 合法")
        return 0
    print("[probe] TTS 门未通过：见上方 FAIL 项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
