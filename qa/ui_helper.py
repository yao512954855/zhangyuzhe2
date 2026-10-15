# -*- coding: utf-8 -*-
"""真机 UI 冒烟助手：dump -> 找控件 -> 点击/读取。
用法：
  python ui_helper.py read                     # 打印当前屏幕文本
  python ui_helper.py tap <关键词>              # 点击包含关键词的控件中心
  python ui_helper.py has <关键词>              # 是否存在含关键词的控件
"""
import os
import re
import subprocess
import sys

ADB = "adb"
TMP = os.environ.get("TEMP", "/tmp")
XML = os.path.join(TMP, "bjsys_ui.xml")


def dump() -> str:
    subprocess.run([ADB, "shell", "uiautomator", "dump", "/sdcard/bjsys_ui.xml"],
                   capture_output=True, timeout=30)
    subprocess.run([ADB, "pull", "/sdcard/bjsys_ui.xml", XML],
                   capture_output=True, timeout=30)
    with open(XML, encoding="utf-8", errors="ignore") as f:
        return f.read()


def nodes(xml: str):
    out = []
    for m in re.finditer(r'<node[^>]*?/?>', xml):
        tag = m.group(0)

        def attr(k):
            mm = re.search(k + r'="([^"]*)"', tag)
            return mm.group(1) if mm else ""

        text = attr("text").replace("&#10;", " ").replace("&amp;", "&")
        desc = attr("content-desc").replace("&#10;", " ").replace("&amp;", "&")
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
        box = tuple(int(x) for x in b.groups()) if b else None
        if text or desc:
            out.append({"text": text, "desc": desc, "box": box})
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"
    xml = dump()
    ns = nodes(xml)
    if cmd == "read":
        for n in ns:
            label = n["text"] or n["desc"]
            print(f"{label}  {n['box']}")
    elif cmd == "tap":
        kw = sys.argv[2]
        for n in ns:
            if kw in n["text"] or kw in n["desc"]:
                x = (n["box"][0] + n["box"][2]) // 2
                y = (n["box"][1] + n["box"][3]) // 2
                subprocess.run([ADB, "shell", "input", "tap", str(x), str(y)], timeout=15)
                print(f"已点击[{kw}] @({x},{y})")
                return
        print(f"未找到: {kw}")
        sys.exit(1)
    elif cmd == "has":
        kw = sys.argv[2]
        found = any(kw in n["text"] or kw in n["desc"] for n in ns)
        print("YES" if found else "NO")
        sys.exit(0 if found else 2)


if __name__ == "__main__":
    main()
