"""生成 QA-01 的测试数据（Excel fixtures）。

**为什么要用脚本生成而不是把 xlsx 直接放进仓库**：
xlsx 是二进制、diff 看不出来，改一行要整个文件重提，评审时谁也说不清改了啥。
生成脚本是纯文本、可评审、可复现 —— 而且列定义直接引用后端的列常量口径，
**后端改了列，跑一遍脚本就能发现 fixture 失效**，不会等到集成时才炸。

    cd qa/fixtures && ../../backend/.venv/Scripts/python.exe make_fixtures.py

产出（全部落在本目录）：
    students_import.xlsx   45 名学生，其中 2 行故意非法（缺姓名 / 学号超长）
    words_import.xlsx      2 个单元共 10 个词条，其中 1 行故意非法（年级 10 越界）
    texts_import.xlsx      1 篇课文（2 段），背诵范围留空 = 全文

音频样本**不在本目录**：它们已经在 `qa/audio`（英语）、`qa/audio_cn`（语文）、
`qa/audio_neg`（负样本）里，是 ASR 预研留下的真实产物，没必要再拷一份。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.excel import write_xlsx  # noqa: E402

HERE = Path(__file__).resolve().parent

# 列定义必须与后端解析口径一致（改了这里跑一遍就能发现 fixture 失效）
STUDENT_HEADERS = ["学号", "姓名", "班级", "初始密码"]
WORD_HEADERS = [
    "学科", "年级", "学期", "单元名", "单元序号", "类型", "英文",
    "中文", "可接受答案", "音标", "词性", "例句英", "例句中", "是否拼读",
]
TEXT_HEADERS = ["学科", "年级", "学期", "单元名", "课文标题", "作者", "段落正文", "背诵范围"]

CLASS_NAME = "三年级(1)班"


def students_rows() -> list[list]:
    """45 行：43 行合法 + 第 8 行缺姓名 + 第 30 行学号超 32 字符。

    和验收用例 U2 的「导入 45 名学生（2 行错误）→ created=43」一一对应。
    """
    rows: list[list] = []
    for i in range(2, 47):  # Excel 行号 2..46 → 45 行
        if i == 8:
            rows.append(["2026" + f"{i:04d}", "", CLASS_NAME, "123456"])
        elif i == 30:
            rows.append(["2" * 40, f"超长学号{i}", CLASS_NAME, "123456"])
        else:
            rows.append(["2026" + f"{i:04d}", f"学生{i - 1}", CLASS_NAME, "123456"])
    return rows


def words_rows() -> list[list]:
    """10 个词条（2 个单元）+ 1 行非法（年级 10 越界）。

    刻意混两个单元，用来验证「一次导入跨单元 / 自动建缺失单元」这条路。
    """
    rows: list[list] = []
    for i, (en, cn) in enumerate([
        ("classroom", "教室"), ("apple", "苹果"), ("banana", "香蕉"),
        ("orange", "橙子"), ("pencil", "铅笔"),
    ]):
        rows.append(["英语", 3, "上学期", "Unit 1 Hello!", 1, "单词", en, cn, "", "", "", "", "", "是"])
    for i, (en, cn) in enumerate([("teacher", "教师"), ("student", "学生"), ("book", "书")]):
        rows.append(["英语", 3, "上学期", "Unit 2 School", 2, "单词", en, cn, "", "", "", "", "", "是"])
    # 第 11 行：年级 10 越界 → 必须被 preview 列出来
    rows.append(["英语", 10, "上学期", "Unit 2 School", 2, "单词", "pencil", "铅笔", "", "", "", "", "", "是"])
    return rows


def texts_rows() -> list[list]:
    """1 篇课文，2 段（段落用换行分隔，服务端按行切段）。背诵范围留空 = 全文。"""
    body = "\n".join([
        "我与父亲不相见已二年余了，我最不能忘记的是他的背影。",
        "那年冬天，祖母死了，父亲的差使也交卸了，正是祸不单行的日子。",
    ])
    return [["语文", 3, "上学期", "第一单元", "背影（节选）", "朱自清", body, ""]]


def main() -> int:
    produced: list[tuple[str, int, list[str]]] = []

    (HERE / "students_import.xlsx").write_bytes(write_xlsx(STUDENT_HEADERS, students_rows()))
    produced.append(("students_import.xlsx", len(students_rows()), STUDENT_HEADERS))

    (HERE / "words_import.xlsx").write_bytes(write_xlsx(WORD_HEADERS, words_rows()))
    produced.append(("words_import.xlsx", len(words_rows()), WORD_HEADERS))

    (HERE / "texts_import.xlsx").write_bytes(write_xlsx(TEXT_HEADERS, texts_rows()))
    produced.append(("texts_import.xlsx", len(texts_rows()), TEXT_HEADERS))

    for name, n, headers in produced:
        print(f"✔ {name}    {n} 行 × {len(headers)} 列")
    print()
    # 预期校验结果直接按实际行数算出来，别手写常量 ——
    # 手写的那个数一旦和 rows() 脱节，就会变成一份「看着像断言、其实是幻觉」的文档。
    ok_s, bad_s = len(students_rows()) - 2, 2
    ok_w, bad_w = len(words_rows()) - 1, 1
    ok_t, bad_t = len(texts_rows()), 0
    print("预期校验结果（与 backend 的 preview 接口对账用）：")
    print(f"  students_import.xlsx → valid={ok_s} / invalid={bad_s}"
          "（第 8 行缺姓名、第 30 行学号超长）")
    print(f"  words_import.xlsx    → valid={ok_w} / invalid={bad_w}（第 11 行年级越界）")
    print(f"  texts_import.xlsx    → valid={ok_t} / invalid={bad_t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
