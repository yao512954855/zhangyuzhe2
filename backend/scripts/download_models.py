"""本地化模型下载脚本 —— 契约《docs/localize-dev/01-契约冻结》§3。

幂等：目标目录已存在且非空则跳过。`--only tts|asr` 可只下某一组。
下载源按契约顺序尝试：GitHub 直连 → ghproxy 镜像前缀。

用法：
    python scripts/download_models.py            # 全部
    python scripts/download_models.py --only tts
    python scripts/download_models.py --only asr

模型落盘：backend/models/{tts,asr}/<模型名>/（该目录不入库）。
依赖只有标准库（urllib），不依赖 requests/httpx——脚本要在裸环境也能跑。
⚠️ 运行必须 `env -u HTTP_PROXY -u HTTPS_PROXY`（会话注入的慢代理会坑死大文件下载）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"

GH = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
GH_PROXY = "https://ghproxy.cn/"

# 契约 §3 冻结的模型清单（首选 + 备选顺序）。url 里的 {} 会被 GH/GH_PROXY 替换。
SPECS: dict[str, list[dict]] = {
    "tts": [
        {
            "name": "vits-piper-en_US-amy-medium",
            "kind": "tar.bz2",
            "urls": [f"{GH}/tts-models/vits-piper-en_US-amy-medium.tar.bz2"],
            "detail": "Piper 英文（听写报单词）",
        },
        {
            "name": "vits-melo-tts-zh_en",
            "kind": "tar.bz2",
            "urls": [f"{GH}/tts-models/vits-melo-tts-zh_en.tar.bz2"],
            "detail": "melo 中英混合（报中文意思）",
        },
    ],
    "asr": [
        {
            "name": "vosk-model-small-en-us-0.15",
            "kind": "zip",
            "urls": [
                "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
            ],
            "detail": "Vosk 英文字母拼读（grammar 锁 26 字母）",
        },
        # 契约修订 v1.0.2（2026-10-13）：streaming-zipformer-bilingual-zh-en-2023-02-20
        # 预研双门 FAIL，换 SenseVoice small int8（离线，16k，带 ITN）。
        {
            "name": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
            "kind": "tar.bz2",
            "urls": [
                f"{GH}/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
            ],
            "detail": "SenseVoice small int8 中英离线转写（看英文背中文/背诵）",
        },
    ],
}


def _fetch(url: str, dest: Path) -> None:
    """下载 url → dest（文件）。源顺序：直连 → ghproxy 前缀。"""
    candidates = [url] + [f"{GH_PROXY}{u}" for u in [url]]
    last_exc: Exception | None = None
    for u in candidates:
        try:
            print(f"  下载 {u}")
            with urllib.request.urlopen(u, timeout=60) as resp, open(dest, "wb") as f:
                shutil.copyfileobj(resp, f, length=1024 * 1024)
            size_mb = dest.stat().st_size / 1024 / 1024
            if size_mb < 1:
                raise RuntimeError(f"文件过小 {size_mb:.2f}MB，疑似错误页")
            print(f"  完成 {dest.name} {size_mb:.1f}MB")
            return
        except Exception as exc:  # noqa: BLE001 - 换下一个源
            print(f"  源失败：{exc}")
            last_exc = exc
            if dest.exists():
                dest.unlink()
    raise RuntimeError(f"所有源均失败：{last_exc}")


def _unpack(archive: Path, target: Path) -> None:
    """解包到 models/<group>/<模型名>/；压缩包内通常自带同名顶层目录。"""
    target.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    else:
        with tarfile.open(archive, "r:bz2") as t:
            t.extractall(target)
    # 若解出「唯一子目录」，把内容上提一层，路径规整为 models/<group>/<name>/...
    entries = [e for e in target.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for item in inner.iterdir():
            shutil.move(str(item), str(target / item.name))
        inner.rmdir()


def download(group: str) -> None:
    ok_all = True
    for spec in SPECS[group]:
        target = MODELS_DIR / group / spec["name"]
        marker = target / ".onnx" if spec["name"].endswith("sherpa") else None
        # 已存在且含文件 → 跳过（幂等）
        if target.is_dir() and any(target.iterdir()):
            print(f"[skip] {group}/{spec['name']} 已存在")
            continue
        print(f"[get ] {group}/{spec['name']} —— {spec['detail']}")
        try:
            with tempfile.TemporaryDirectory() as td:
                archive = Path(td) / f"dl.{spec['kind']}"
                _fetch(spec["urls"][0], archive)
                _unpack(archive, target)
            print(f"[ ok ] {group}/{spec['name']}")
        except Exception as exc:  # noqa: BLE001 - 单模型失败不拖垮整批
            ok_all = False
            print(f"[FAIL] {group}/{spec['name']}：{exc}")
            print("       备选模型见 localize-dev/01 §3；或手动放置后重跑（幂等跳过）")
    if not ok_all:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["tts", "asr"], help="只下某组模型")
    args = parser.parse_args()
    groups = [args.only] if args.only else ["tts", "asr"]
    for g in groups:
        download(g)
    print("模型目录：", MODELS_DIR)


if __name__ == "__main__":
    main()
