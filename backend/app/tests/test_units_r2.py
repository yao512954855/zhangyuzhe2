"""BE-11 验收 —— 单元两级树（todo11 B1 / 契约 v1.3 §3.1b / §3.4 / §4.1）。

用例对应《50-二轮后端工作包规格》BE-11（迁移部分 U11-1/U11-2/U11-3 已由主线
验证，本文件只覆盖接口行为）：
- U11-4 GET /api/units：一级 A + 二级 A1/A2（各挂 items）→ A 的 counts = 自身 + 二级汇总
- U11-5 POST parent_id 指向二级 → 报错（只许两级）
- U11-6 PUT 把二级从 A 挪到 B → counts 正确重算
- U11-7 DELETE 一级 → 二级与 items 消失；wrong_book 孤儿行不残留（FK CASCADE）
- U11-8 存量单元（parent_id 空）保持一级，旧接口调用不炸（回归）

另覆盖：PUT 校验（parent 不存在 / self / 带二级移动 / 显式 null 升一级）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be11_units_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
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


def _mk_unit(client: TestClient, name: str, sort: int = 0, parent_id: int | None = None) -> int:
    r = client.post("/api/units", json={"name": name, "sort": sort, "parent_id": parent_id})
    assert r.status_code == 200 and r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


def _mk_item(client: TestClient, unit_id: int, type_: str, en: str, zh: str) -> int:
    r = client.post("/api/items", json={"unit_id": unit_id, "type": type_, "en": en, "zh": zh})
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


def _find(tree: list[dict], unit_id: int) -> dict | None:
    for root in tree:
        if root["id"] == unit_id:
            return root
        for child in root["children"]:
            if child["id"] == unit_id:
                return child
    return None


# ===========================================================================
# U11-4：两级树 + counts 汇总
# ===========================================================================

def test_u11_4_two_level_tree_counts(client: TestClient):
    """一级 A + 二级 A1/A2（各挂 items）→ A 的 counts = 自身 + 二级汇总。"""
    a = _mk_unit(client, "三年级上册", sort=1)
    b = _mk_unit(client, "三年级下册", sort=2)
    a1 = _mk_unit(client, "Unit 1", sort=1, parent_id=a)
    a2 = _mk_unit(client, "Unit 2", sort=2, parent_id=a)

    # A 自身 1 word；A1 挂 2 word + 1 phrase；A2 挂 1 sentence
    _mk_item(client, a, "word", "self", "自身")
    _mk_item(client, a1, "word", "apple", "苹果")
    _mk_item(client, a1, "word", "cat", "猫")
    _mk_item(client, a1, "phrase", "good morning", "早上好")
    _mk_item(client, a2, "sentence", "I am a boy.", "我是一个男孩。")
    _mk_item(client, b, "word", "dog", "狗")

    r = client.get("/api/units")
    assert r.status_code == 200 and r.json()["code"] == 0
    tree = r.json()["data"]

    ra = _find(tree, a)
    assert ra is not None and ra["parent_id"] is None
    assert ra["counts"] == {"word": 3, "phrase": 1, "sentence": 1}, "A = 自身1word + A1(2w,1p) + A2(1s)"
    assert [c["id"] for c in ra["children"]] == [a1, a2], "二级按 sort 升序"
    for child in ra["children"]:
        assert child["parent_id"] == a
        assert child["children"] == []

    ra1 = _find(tree, a1)
    assert ra1["counts"] == {"word": 2, "phrase": 1, "sentence": 0}
    ra2 = _find(tree, a2)
    assert ra2["counts"] == {"word": 0, "phrase": 0, "sentence": 1}

    rb = _find(tree, b)
    assert rb["counts"] == {"word": 1, "phrase": 0, "sentence": 0}
    assert rb["children"] == []

    # 结构完整性：每个节点字段与契约 §4.1 一致
    for node in (ra, rb, *ra["children"]):
        assert set(node.keys()) == {"id", "name", "sort", "parent_id", "counts", "children"}
        assert set(node["counts"].keys()) == {"word", "phrase", "sentence"}


# ===========================================================================
# U11-5：POST 校验（只许两级）
# ===========================================================================

def test_u11_5_post_parent_must_be_level1(client: TestClient):
    a = _mk_unit(client, "U11-5 一级", sort=9)
    a1 = _mk_unit(client, "U11-5 二级", sort=1, parent_id=a)

    # parent_id 指向二级 → 报错（只许两级）
    r = client.post("/api/units", json={"name": "三级", "parent_id": a1})
    assert r.json()["code"] == 10001

    # parent_id 指向不存在的单元 → 30001
    r = client.post("/api/units", json={"name": "挂空", "parent_id": 999999})
    assert r.json()["code"] == 30001

    # 正常：parent_id 指向一级 → 成功
    r = client.post("/api/units", json={"name": "U11-5 合法二级", "parent_id": a})
    assert r.json()["code"] == 0
    assert r.json()["data"]["parent_id"] == a


# ===========================================================================
# U11-6：PUT 移动归属 → counts 重算
# ===========================================================================

def test_u11_6_put_move_between_parents(client: TestClient):
    a = _mk_unit(client, "U11-6 A", sort=20)
    b = _mk_unit(client, "U11-6 B", sort=21)
    a1 = _mk_unit(client, "U11-6 A1", sort=1, parent_id=a)
    _mk_item(client, a1, "word", "move", "移动")
    _mk_item(client, a, "word", "stay", "留下")

    # 移动前：A counts 含 A1
    tree = client.get("/api/units").json()["data"]
    assert _find(tree, a)["counts"]["word"] == 2
    assert _find(tree, b)["counts"]["word"] == 0

    # PUT 二级从 A 挪到 B（items 不动，只挪挂载点）
    r = client.put(f"/api/units/{a1}", json={"parent_id": b})
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["parent_id"] == b

    tree = client.get("/api/units").json()["data"]
    assert _find(tree, a)["counts"]["word"] == 1, "A 失去 A1 的汇总"
    assert _find(tree, b)["counts"]["word"] == 1, "B 获得 A1 的汇总"
    assert [c["id"] for c in _find(tree, a)["children"]] == []
    assert [c["id"] for c in _find(tree, b)["children"]] == [a1]
    # items 跟着二级走，没被删
    r = client.get("/api/items", params={"unit_id": a1})
    assert r.json()["data"]["total"] == 1


def test_u11_6_put_parent_validations(client: TestClient):
    a = _mk_unit(client, "U11-6v A", sort=30)
    a1 = _mk_unit(client, "U11-6v A1", sort=1, parent_id=a)
    b1 = _mk_unit(client, "U11-6v B1", sort=1, parent_id=a)

    # 挪到二级下 → 10001
    r = client.put(f"/api/units/{b1}", json={"parent_id": a1})
    assert r.json()["code"] == 10001
    # parent 不存在 → 30001
    r = client.put(f"/api/units/{a1}", json={"parent_id": 999999})
    assert r.json()["code"] == 30001
    # 挂到自己 → 10001
    r = client.put(f"/api/units/{a1}", json={"parent_id": a1})
    assert r.json()["code"] == 10001
    # 带着二级的一级挪到别人下（会变三级）→ 10001
    r = client.put(f"/api/units/{a}", json={"parent_id": b1})
    assert r.json()["code"] == 10001
    # 一级挪到自己 → 10001
    r = client.put(f"/api/units/{a}", json={"parent_id": a})
    assert r.json()["code"] == 10001

    # 显式 null = 升为一级
    r = client.put(f"/api/units/{b1}", json={"parent_id": None})
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["parent_id"] is None
    tree = client.get("/api/units").json()["data"]
    node = _find(tree, b1)
    assert node["parent_id"] is None and node["children"] == []

    # 不传 parent_id → 归属不变
    r = client.put(f"/api/units/{a1}", json={"sort": 5})
    assert r.json()["code"] == 0
    assert r.json()["data"]["parent_id"] == a
    assert r.json()["data"]["sort"] == 5


# ===========================================================================
# U11-7：DELETE 一级 → 二级 + items 级联，wrong_book 不残留
# ===========================================================================

def test_u11_7_delete_cascades(client: TestClient):
    a = _mk_unit(client, "U11-7 A", sort=40)
    a1 = _mk_unit(client, "U11-7 A1", sort=1, parent_id=a)
    a2 = _mk_unit(client, "U11-7 A2", sort=2, parent_id=a)

    item1 = _mk_item(client, a1, "word", "del1", "删一")
    item2 = _mk_item(client, a2, "phrase", "del2", "删二")

    # 造一条未移出错题（wrong_book.item_id → items FK CASCADE）
    with write_tx() as conn:
        conn.execute(
            "INSERT INTO wrong_book (item_id, mode, wrong_count, last_wrong_at) VALUES (?, 'listen', 1, ?)",
            (item1, "2026-10-16T00:00:00Z"),
        )

    assert client.delete(f"/api/units/{a}").json()["code"] == 0

    tree = client.get("/api/units").json()["data"]
    assert _find(tree, a) is None
    assert _find(tree, a1) is None
    assert _find(tree, a2) is None

    with read_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM book_units WHERE id IN (?, ?, ?)", (a, a1, a2)).fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM items WHERE unit_id IN (?, ?)", (a1, a2)).fetchone()["n"] == 0, "items 级联删未生效"
        assert conn.execute("SELECT COUNT(*) AS n FROM wrong_book WHERE item_id = ?", (item1,)).fetchone()["n"] == 0, "wrong_book 孤儿行残留"

    # DELETE 二级 → 其 items 级联；一级保留
    b = _mk_unit(client, "U11-7 B", sort=41)
    b1 = _mk_unit(client, "U11-7 B1", sort=1, parent_id=b)
    _mk_item(client, b1, "word", "del3", "删三")
    assert client.delete(f"/api/units/{b1}").json()["code"] == 0
    tree = client.get("/api/units").json()["data"]
    assert _find(tree, b) is not None and _find(tree, b1) is None
    with read_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM items WHERE unit_id = ?", (b1,)).fetchone()["n"] == 0

    # DELETE 不存在的单元 → 30001
    r = client.delete("/api/units/999999")
    assert r.json()["code"] == 30001


# ===========================================================================
# U11-8：存量单元（parent_id 空）保持一级，旧接口行为回归
# ===========================================================================

def test_u11_8_legacy_units_stay_level1(client: TestClient):
    """模拟存量库：直接插入 parent_id 空的行（003 形态），接口不炸、保持一级。"""
    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO book_units (name, sort, created_at) VALUES (?, ?, ?)",
            ("Starter Unit 1（存量）", 99, "2026-01-01T00:00:00Z"),
        )
        legacy_id = int(cur.lastrowid or 0)
        conn.execute(
            "INSERT INTO items (unit_id, type, en, pos, zh, alt_zh, sort, created_at)"
            " VALUES (?, 'word', 'legacy', '', '存量', '', 0, '2026-01-01T00:00:00Z')",
            (legacy_id,),
        )

    # 树：存量单元按一级展示
    tree = client.get("/api/units").json()["data"]
    node = _find(tree, legacy_id)
    assert node is not None
    assert node["parent_id"] is None and node["children"] == []
    assert node["counts"] == {"word": 1, "phrase": 0, "sentence": 0}

    # 旧调用形态回归：POST 不带 parent_id = 一级；PUT 只改 name/sort；DELETE 正常
    r = client.post("/api/units", json={"name": "旧式新增", "sort": 100})
    assert r.json()["code"] == 0 and r.json()["data"]["parent_id"] is None
    new_id = r.json()["data"]["id"]

    r = client.put(f"/api/units/{legacy_id}", json={"name": "存量改名"})
    assert r.json()["code"] == 0 and r.json()["data"]["name"] == "存量改名"
    assert r.json()["data"]["parent_id"] is None, "不传 parent_id 不得改动归属"

    assert client.delete(f"/api/units/{new_id}").json()["code"] == 0
    tree = client.get("/api/units").json()["data"]
    assert _find(tree, new_id) is None
