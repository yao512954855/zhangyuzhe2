"""WP-A 验收 —— 内容条目（U3 判重与空 en / U4 Excel 导入 / U5 导出与模板）。

对应《10-后端工作包规格》WP-A 验收用例 U3 / U4 / U5。
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_s2_items_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.excel import read_xlsx  # noqa: E402
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


@pytest.fixture(scope="module")
def unit_id(client: TestClient) -> int:
    r = client.post("/api/units", json={"name": "Unit A", "sort": 1})
    assert r.json()["code"] == 0
    return r.json()["data"]["id"]


def _mk_xlsx(rows: list[tuple[str, str, str, str]]) -> bytes:
    """构造导入用 xlsx（表头 + 数据行）。"""
    wb = load_workbook(io.BytesIO()) if False else None  # noqa: F841 - 直接用 write 路径
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["英文", "词性", "中文", "其他可接受答案"])
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ===========================================================================
# U3：items CRUD —— 重复 en → 20003；空 en → 10001
# ===========================================================================

def test_u3_duplicate_en_returns_20003(client: TestClient, unit_id: int):
    r = client.post(
        "/api/items", json={"unit_id": unit_id, "type": "word", "en": "apple", "zh": "苹果"}
    )
    assert r.json()["code"] == 0
    # 同 unit + type 下重复 → 20003
    r = client.post(
        "/api/items", json={"unit_id": unit_id, "type": "word", "en": "apple", "zh": "苹果2"}
    )
    assert r.json()["code"] == 20003, r.json()
    # 同 unit 不同 type 不算重复
    r = client.post(
        "/api/items", json={"unit_id": unit_id, "type": "phrase", "en": "apple", "zh": "苹果牌"}
    )
    assert r.json()["code"] == 0
    # 不同 unit 同 type 不算重复
    r2 = client.post("/api/units", json={"name": "Unit B", "sort": 2})
    other = r2.json()["data"]["id"]
    r = client.post(
        "/api/items", json={"unit_id": other, "type": "word", "en": "apple", "zh": "苹果"}
    )
    assert r.json()["code"] == 0


def test_u3_empty_en_returns_10001(client: TestClient, unit_id: int):
    r = client.post(
        "/api/items", json={"unit_id": unit_id, "type": "word", "en": "   ", "zh": "空格"}
    )
    assert r.json()["code"] == 10001, r.json()
    assert "英文" in r.json()["message"]


def test_u3_update_delete_and_validation(client: TestClient, unit_id: int):
    # 更新：改 zh
    r = client.post(
        "/api/items", json={"unit_id": unit_id, "type": "word", "en": "dog", "zh": "狗"}
    )
    item_id = r.json()["data"]["id"]
    r = client.put(f"/api/items/{item_id}", json={"zh": "小狗"})
    assert r.json()["data"]["zh"] == "小狗"
    # 更新成重复 en → 20003
    r = client.put(f"/api/items/{item_id}", json={"en": "apple"})
    assert r.json()["code"] == 20003
    # 空 zh → 10001
    r = client.put(f"/api/items/{item_id}", json={"zh": ""})
    assert r.json()["code"] == 10001
    # 删除
    assert client.delete(f"/api/items/{item_id}").json()["code"] == 0
    r = client.get("/api/items", params={"unit_id": unit_id, "keyword": "dog"})
    assert r.json()["data"]["total"] == 0
    # 删不存在 → 30002
    r = client.delete(f"/api/items/{item_id}")
    assert r.json()["code"] == 30002


def test_u3_list_pagination_and_keyword(client: TestClient, unit_id: int):
    # 专用数据：不依赖其他用例的遗留行
    made = []
    for en in ("aa1", "aa2", "aa3"):
        rr = client.post(
            "/api/items", json={"unit_id": unit_id, "type": "word", "en": en, "zh": f"释{en}"}
        )
        assert rr.json()["code"] == 0
        made.append(rr.json()["data"]["id"])

    r = client.get("/api/items", params={"unit_id": unit_id, "page": 1, "page_size": 2})
    body = r.json()["data"]
    assert body["page"] == 1 and body["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["total"] >= 3
    # 翻页
    r2 = client.get("/api/items", params={"unit_id": unit_id, "page": 2, "page_size": 2})
    ids_p1 = {it["id"] for it in body["items"]}
    ids_p2 = {it["id"] for it in r2.json()["data"]["items"]}
    assert not (ids_p1 & ids_p2), "两页不重叠"
    r = client.get("/api/items", params={"keyword": "aa1"})
    items = r.json()["data"]["items"]
    assert any(it["en"] == "aa1" for it in items)


# ===========================================================================
# U4：Excel 导入 —— 含重复行 / 坏行
# ===========================================================================

def test_u4_import_with_duplicates_and_bad_rows(client: TestClient, unit_id: int):
    raw = _mk_xlsx(
        [
            ("pig", "n.", "猪", ""),
            ("pig", "", "猪", ""),          # 文件内重复 → duplicated
            ("bird", "", "", "小鸟"),       # 坏行：缺中文 → failed
            ("cat", "", "", ""),            # 坏行：缺中文 → failed
            ("", "", "有中文没英文", ""),    # 坏行：缺英文 → failed
        ]
    )
    r = client.post(
        "/api/items/import",
        files={"file": ("words.xlsx", raw)},
        data={"unit_id": str(unit_id), "type": "word"},
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["inserted"] == 1, body
    assert body["duplicated"] == 1
    failed = body["failed"]
    assert len(failed) == 3, failed
    assert failed[0]["row"] == 4 and "中文" in failed[0]["reason"]
    assert failed[1]["row"] == 5 and "中文" in failed[1]["reason"]
    assert failed[2]["row"] == 6 and "英文" in failed[2]["reason"]

    # 再导一次：与库内重复 → duplicated
    raw2 = _mk_xlsx([("pig", "n.", "猪", "")])
    r = client.post(
        "/api/items/import",
        files={"file": ("words.xlsx", raw2)},
        data={"unit_id": str(unit_id), "type": "word"},
    )
    assert r.json()["data"]["duplicated"] == 1
    assert r.json()["data"]["inserted"] == 0


def test_u4_import_bad_unit_or_type(client: TestClient, unit_id: int):
    raw = _mk_xlsx([("cow", "", "牛", "")])
    r = client.post(
        "/api/items/import",
        files={"file": ("w.xlsx", raw)},
        data={"unit_id": "999999", "type": "word"},
    )
    assert r.json()["code"] == 30001  # 单元不存在
    r = client.post(
        "/api/items/import",
        files={"file": ("w.xlsx", raw)},
        data={"unit_id": str(unit_id), "type": "verb"},
    )
    assert r.json()["code"] == 10001


# ===========================================================================
# U5：导出回读 + 模板可下载且能被导入
# ===========================================================================

def test_u5_export_roundtrip(client: TestClient, unit_id: int):
    r = client.get("/api/items/export", params={"unit_id": unit_id, "type": "word"})
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    rows = read_xlsx(r.content)
    ens = {row["英文"] for row in rows}
    assert {"apple", "pig"} <= ens
    first = next(row for row in rows if row["英文"] == "apple")
    assert first["中文"] == "苹果"
    assert first["其他可接受答案"] == "" or first["其他可接受答案"] is None


def test_u5_template_downloadable_and_importable(client: TestClient, unit_id: int):
    r = client.get("/api/items/import-template")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]

    # 模板可被导入（示例行不自动入库——模板示例在独立「填写示例」工作表）
    r2 = client.post(
        "/api/items/import",
        files={"file": ("items_template.xlsx", r.content)},
        data={"unit_id": str(unit_id), "type": "word"},
    )
    assert r2.status_code == 200 and r2.json()["code"] == 0, r2.json()

    # 模板副本落盘（backend/templates/items_template.xlsx）
    from app.core.config import BASE_DIR

    assert (BASE_DIR / "templates" / "items_template.xlsx").exists()
