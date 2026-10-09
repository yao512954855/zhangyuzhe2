# -*- coding: utf-8 -*-
"""OCR 实测：RecognizeHandwriting 通用手写体识别。

造一张手写风格听写答题图 -> 二进制 body 调阿里云 -> 打印识别结果。
用法：python _ocr_probe.py
"""
import io
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont

# ---- 1. 造测试图：模仿学生在答题区竖排/横排写单词 ----
WORDS = ["apple", "banana", "cat", "dog", "school", "happy"]

img = Image.new("RGB", (900, 600), "white")
draw = ImageDraw.Draw(img)
font = ImageFont.truetype(r"C:\Windows\Fonts\segoesc.ttf", 64)
title_font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 28)

draw.text((30, 15), "Dictation - Unit 1", font=title_font, fill="#555555")
# 两列三行，模拟答题排版
for i, w in enumerate(WORDS):
    col, row = divmod(i, 3)
    x = 80 + col * 420
    y = 90 + row * 160
    draw.text((x, y), f"{i+1}. {w}", font=font, fill="#1a1a5e")

buf = io.BytesIO()
img.save(buf, format="JPEG", quality=92)
image_bytes = buf.getvalue()
img.save(os.path.join(os.path.dirname(__file__), "_ocr_test_input.jpg"))
print(f"测试图已生成：{len(image_bytes)/1024:.0f} KB，写入单词 = {WORDS}")

# ---- 2. 调 RecognizeHandwriting ----
from alibabacloud_ocr_api20210707.client import Client
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ocr_api20210707 import models as ocr_models


def make_client():
    cfg = open_api_models.Config(
        access_key_id=os.environ["OCR_ACCESS_KEY_ID"],
        access_key_secret=os.environ["OCR_ACCESS_KEY_SECRET"],
        endpoint="ocr-api.cn-hangzhou.aliyuncs.com",
    )
    return Client(cfg)


def recognize(client, body: bytes) -> dict:
    req = ocr_models.RecognizeHandwritingRequest(
        body=body,
        need_rotate=True,
        output_char_info=True,
    )
    runtime = __import__("alibabacloud_tea_util.models", fromlist=["RuntimeOptions"]).RuntimeOptions()
    resp = client.recognize_handwriting_with_options(req, runtime)
    return resp.body.to_map() if resp.body else {}


# 读 .env 里的 AK
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
with open(env_path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

try:
    client = make_client()
    result = recognize(client, image_bytes)
except Exception as e:  # noqa: BLE001
    print("调用失败：", type(e).__name__, str(e)[:600])
    sys.exit(1)

# ---- 3. 解析结果 ----
print("\n===== 原始返回（截断）=====")
print(json.dumps(result, ensure_ascii=False)[:800])

data_raw = result.get("Data") or result.get("data") or "{}"
data = json.loads(data_raw) if isinstance(data_raw, str) else data_raw
blocks = data.get("prism_wordsInfo") or []
recognized = [b.get("word", "") for b in blocks]
print("\n===== 识别出的文字块 =====")
for b in blocks:
    print(f"  word={b.get('word')!r}  prob={b.get('prob')}  x={b.get('x')} y={b.get('y')}")

# ---- 4. 模拟批改：按 y,x 排序后与词表比对 ----
def normalize(w: str) -> str:
    w = w.lower().strip()
    # 去掉题号前缀 "1." 之类
    for sep in (".", "、", "．", " ", ")", "）"):
        if sep in w:
            w = w.split(sep, 1)[1] if w.split(sep, 1)[0].strip().isdigit() else w
    return w.strip()

sorted_blocks = sorted(blocks, key=lambda b: (round((b.get("y") or 0) / 60), b.get("x") or 0))
got = [normalize(b.get("word", "")) for b in sorted_blocks]
got = [g for g in got if g]
expected = [w.lower() for w in WORDS]

print("\n===== 模拟批改 =====")
correct = 0
for i, exp in enumerate(expected):
    hit = exp in got
    correct += hit
    print(f"  {exp:<8} -> {'✓ 对' if hit else '✗ 未识别到'}")
print(f"正确 {correct}/{len(expected)}，错误 {len(expected)-correct}")
