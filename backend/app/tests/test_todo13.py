"""todo13 第一批验收 —— 叶节点校验 / 批量删除移动 / 类型列导入导出 / 竖线导入。

需求来源：2026-10-19 甲方反馈（todo13.md），2026-10-22 姚工确认方案：
- R1  叶节点才能录内容：一级分组（有 children）录入/导入/移动目标 → 10001
- R2  批量删除 {deleted, missing} / 批量移动 {moved, skipped}（目标叶 + 同 type 判重跳过）
- R3  导出加「类型」列（三种混导）；导入逐行按类型列，旧文件无列回落表单 type
- R4  竖线粘贴导入：2 段=英文|中文，3 段=英文|词性|中文（sentence 中间段并入中文），
      1 段 / >3 段行失败带行号；判重合并语义与 Excel 导入一致
- R5  settings 加 dictation_speech_rate（0~100，默认 45），PUT 白名单校验
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_todo13_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.main import app  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _fresh_config():
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE
    SettingsService._cache = None
    rate_limit.clear_all()
    return _DB_FILE


@pytest.fixture(scope="module")
def client(_fresh_config):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 夹具 helper
# ---------------------------------------------------------------------------

def _mk_unit(client: TestClient, name: str, parent_id: int | None = None) -> int:
    r = client.post("/api/units", json={"name": name, "sort": 0, "parent_id": parent_id})
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


def _mk_item(client: TestClient, unit_id: int, type_: str, en: str, zh: str) -> int:
    r = client.post("/api/items", json={"unit_id": unit_id, "type": type_, "en": en, "zh": zh})
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


def _xlsx(rows: list[list[str]], headers: list[str] | None = None) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(headers or ["英文", "词性", "中文", "其他可接受答案", "类型"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ===========================================================================
# R1 叶节点校验
# ===========================================================================

def test_r1_leaf_unit_rejects_create_and_import(client: TestClient):
    parent = _mk_unit(client, "R1 一级分组")
    leaf = _mk_unit(client, "R1 二级", parent_id=parent)

    # 录入到一级 → 10001
    r = client.post(
        "/api/items", json={"unit_id": parent, "type": "word", "en": "hi", "zh": "嗨"}
    )
    assert r.json()["code"] == 10001
    assert "末级分类" in r.json()["message"]

    # 录入到二级 → 正常
    r = client.post(
        "/api/items", json={"unit_id": leaf, "type": "word", "en": "hi", "zh": "嗨"}
    )
    assert r.json()["code"] == 0, r.json()

    # Excel 导入到一级 → 10001
    r = client.post(
        "/api/items/import",
        files={"file": ("t.xlsx", _xlsx([["cat", "", "猫", "", "单词"]]), "t.xlsx")},
        data={"unit_id": str(parent), "type": "word"},
    )
    assert r.json()["code"] == 10001

    # 竖线导入到一级 → 10001
    r = client.post(
        "/api/items/import-text",
        json={"text": "dog | 狗", "unit_id": parent, "type": "word"},
    )
    assert r.json()["code"] == 10001


def test_r1_batch_move_target_must_be_leaf(client: TestClient):
    parent = _mk_unit(client, "R1b 一级分组")
    leaf = _mk_unit(client, "R1b 二级", parent_id=parent)
    item_id = _mk_item(client, leaf, "word", "move-me", "移动我")

    # 移动目标是一级分组 → 10001
    r = client.post(
        "/api/items/batch-move",
        json={"ids": [item_id], "target_unit_id": parent},
    )
    assert r.json()["code"] == 10001


# ===========================================================================
# R2 批量删除 / 批量移动
# ===========================================================================

def test_r2_batch_delete(client: TestClient):
    leaf = _mk_unit(client, "R2 删除单元")
    ids = [
        _mk_item(client, leaf, "word", f"w{i}", f"词{i}") for i in range(3)
    ]
    r = client.post("/api/items/batch-delete", json={"ids": ids + [99999]})
    assert r.json()["code"] == 0, r.json()
    data = r.json()["data"]
    assert data["deleted"] == 3
    assert data["missing"] == [99999]

    # 删完列表为空
    r = client.get("/api/items", params={"unit_id": leaf})
    assert r.json()["data"]["total"] == 0

    # 空 ids → 10001
    r = client.post("/api/items/batch-delete", json={"ids": []})
    assert r.json()["code"] == 10001


def test_r2_batch_move_with_conflict_skip(client: TestClient):
    src = _mk_unit(client, "R2 源单元")
    dst = _mk_unit(client, "R2 目标单元")
    a = _mk_item(client, src, "word", "apple", "苹果")
    b = _mk_item(client, src, "word", "banana", "香蕉")
    _mk_item(client, dst, "word", "apple", "苹果")  # 目标已有 apple → b 移动、a 跳过

    r = client.post(
        "/api/items/batch-move", json={"ids": [a, b], "target_unit_id": dst}
    )
    assert r.json()["code"] == 0, r.json()
    data = r.json()["data"]
    assert data["moved"] == 1
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["id"] == a
    assert "已有同名" in data["skipped"][0]["reason"]

    # 源剩 1（apple），目标 2
    assert client.get("/api/items", params={"unit_id": src}).json()["data"]["total"] == 1
    assert client.get("/api/items", params={"unit_id": dst}).json()["data"]["total"] == 2


# ===========================================================================
# R3 导出带类型列 / 导入逐行类型 + 旧文件回落
# ===========================================================================

def test_r3_export_includes_type_column(client: TestClient):
    from openpyxl import load_workbook

    leaf = _mk_unit(client, "R3 导出单元")
    _mk_item(client, leaf, "word", "hello", "你好")
    _mk_item(client, leaf, "phrase", "give up", "放弃")
    _mk_item(client, leaf, "sentence", "I am ok.", "我很好")

    r = client.get("/api/items/export", params={"unit_id": leaf})
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    headers = [c.value for c in next(ws.iter_rows(max_row=1))]
    assert headers[-1] == "类型"
    types = [row[4] for row in ws.iter_rows(min_row=2, values_only=True)]
    assert set(types) == {"单词", "词组", "短句"}


def test_r3_import_respects_type_column_and_fallback(client: TestClient):
    leaf = _mk_unit(client, "R3 导入单元")
    rows = [
        ["cat", "", "猫", "", "单词"],          # 类型列=单词
        ["give up", "", "放弃", "", "词组"],     # 类型列=词组
        ["How are you", "", "你好", "", ""],     # 类型列空 → 回落表单 sentence
    ]
    # 旧文件：无类型列 → 全部按表单 type=word
    r = client.post(
        "/api/items/import",
        files={"file": ("t.xlsx", _xlsx([["cat", "", "猫", ""]]), "t.xlsx")},
        data={"unit_id": str(leaf), "type": "word"},
    )
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["inserted"] == 1

    # 先清掉，再测新文件混导
    r = client.get("/api/items", params={"unit_id": leaf})
    ids = [x["id"] for x in r.json()["data"]["items"]]
    client.post("/api/items/batch-delete", json={"ids": ids})

    r = client.post(
        "/api/items/import",
        files={"file": ("t.xlsx", _xlsx(rows), "t.xlsx")},
        data={"unit_id": str(leaf), "type": "sentence"},
    )
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["inserted"] == 3

    # 三种类型各 1 条
    r = client.get("/api/items", params={"unit_id": leaf})
    types = sorted(x["type"] for x in r.json()["data"]["items"])
    assert types == ["phrase", "sentence", "word"]

    # 类型列写了不认识的值 → 该行失败带行号
    leaf2 = _mk_unit(client, "R3 导入单元2")
    r = client.post(
        "/api/items/import",
        files={"file": ("t.xlsx", _xlsx([["xx", "", "yy", "", "动词"]]), "t.xlsx")},
        data={"unit_id": str(leaf2), "type": "word"},
    )
    assert r.json()["code"] == 0
    assert r.json()["data"]["failed"] == [{"row": 2, "reason": "类型列值无法识别：'动词'（应为 单词/词组/短句）"}]


# ===========================================================================
# R4 竖线粘贴导入
# ===========================================================================

def test_r4_import_text_parsing_and_dedup(client: TestClient):
    leaf = _mk_unit(client, "R4 竖线单元")
    text = (
        "face | n. | 脸，面部，脸色\n"
        "\n"
        "give up | 放弃\n"
        "How are you | 问候 | 你好\n"
        "只有一段没有竖线\n"
        "a | b | c | d\n"
        " | 缺英文\n"
        "face | n. | 脸，面部，脸色\n"  # 与第 1 行重复（文件内 + 类型相同）
    )
    r = client.post(
        "/api/items/import-text",
        json={"text": text, "unit_id": leaf, "type": "word"},
    )
    assert r.json()["code"] == 0, r.json()
    data = r.json()["data"]
    assert data["inserted"] == 3  # face / give up / How are you
    assert data["duplicated"] == 1  # 最后一行 face 重复

    failed = {f["row"]: f["reason"] for f in data["failed"]}
    assert set(failed) == {5, 6, 7}  # 一段 / 四段 / 缺英文（原始行号：1=face, 2=空行…）
    assert "2~3 段" in failed[5]
    assert "2~3 段" in failed[6]
    assert "英文不能为空" in failed[7]

    # 落库校验：face 带词性、give up 无词性
    r = client.get("/api/items", params={"unit_id": leaf, "type": "word"})
    items = {x["en"]: x for x in r.json()["data"]["items"]}
    assert items["face"]["pos"] == "n."
    assert items["give up"]["pos"] == ""

    # 短句类型：3 段中间段并入中文、pos 留空
    leaf2 = _mk_unit(client, "R4 竖线短句")
    r = client.post(
        "/api/items/import-text",
        json={"text": "How are you | 问候语 | 你好", "unit_id": leaf2, "type": "sentence"},
    )
    assert r.json()["code"] == 0
    assert r.json()["data"]["inserted"] == 1
    r = client.get("/api/items", params={"unit_id": leaf2})
    item = r.json()["data"]["items"][0]
    assert item["type"] == "sentence"
    assert item["pos"] == ""
    assert item["zh"] == "问候语 你好"

    # 判重：库里已有 face（word），同样内容再导 → duplicated
    r = client.post(
        "/api/items/import-text",
        json={"text": "face | n. | 脸，面部，脸色", "unit_id": leaf, "type": "word"},
    )
    assert r.json()["data"]["duplicated"] == 1


# ===========================================================================
# R5 听写语速设置
# ===========================================================================

def test_r5_speech_rate_setting(client: TestClient):
    # 默认值 45
    r = client.get("/api/settings")
    assert r.json()["code"] == 0
    assert r.json()["data"]["dictation_speech_rate"] == 45

    # 修改到边界值
    r = client.put("/api/settings", json={"dictation_speech_rate": 0})
    assert r.json()["data"]["dictation_speech_rate"] == 0
    r = client.put("/api/settings", json={"dictation_speech_rate": 100})
    assert r.json()["data"]["dictation_speech_rate"] == 100

    # 越界 / 非整数 → 10001
    for bad in (-1, 101, "fast", True):
        r = client.put("/api/settings", json={"dictation_speech_rate": bad})
        assert r.json()["code"] == 10001, bad

    # 还原默认，避免影响同模块其他用例（本模块无后续用例，双保险）
    client.put("/api/settings", json={"dictation_speech_rate": 45})
