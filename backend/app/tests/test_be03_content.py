"""WP-BE-03 内容管理与 Excel 导入 —— 验收测试（U1 ~ U19 + 补充）。

覆盖：
- F1 ~ F4 单元 CRUD 与删除保护
- F5 双角色词条（admin 全字段 / student 裁剪 + mastery）
- F6 ~ F8 词条改 / 批量状态 / 删除保护
- F9 ~ F10 课文列表与详情（不下发拼音）
- F11 ~ F16 模板、两步式导入、拼音等长校验、错误报告
- F17 拼音服务本身

---

### 本文件的两个坑（BE-02 踩过，这里同样处理）

**① `os.environ` 是进程级的。** 同一次 pytest 运行里会同时收集 BE-01 / BE-02 / BE-03
三个测试模块，谁在跑谁就必须在自己的 fixture 里重新声明一次环境变量，
并断言 `get_settings().db_file` 真的是自己的临时库 —— 否则会被别的模块
改成它的库，测试之间互相污染，且只在混跑时才复现。

**② `SettingsService` 的缓存是类变量。** 每个测试换了新库，但缓存里还是上一轮的
`page_size_default`。fixture 里必须显式清掉，否则「单元分页」这类断言会莫名其妙地飘。

### 关于 U7（拼音失配）

`pypinyin 0.51.0` 对**不认识的汉字**不报错、也不返回空串，而是**原样返回该字符**
（实测：`𠮷` → `'𠮷'`）。这种 token 长度上仍是 1:1，所以单看「等长」查不出来，
但它不是拼音，对齐时会永久漏字且**不报任何错**。
故本包的校验规则比「等长」更严：每个汉字必须产出 `^[a-z]+$` 的拼音。
正文里放一个生僻字就能真实触发，不需要人造脏数据。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be03_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "be03-test-secret-0123456789abcdef"
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "be03-Admin-Pass"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import preview_store, rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.core.excel import write_xlsx  # noqa: E402
from app.main import app  # noqa: E402
from app.services import pinyin_service  # noqa: E402
from app.services.import_service import TEXTS_HEADERS, WORDS_HEADERS  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------------------
# fixture 与工具
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """每个测试一个**全新的空库** + 干净的限流桶 + 干净的 settings 缓存。"""
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "be03-test-secret-0123456789abcdef"
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "be03-Admin-Pass"
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 被同进程里别的测试模块改掉了"

    for suffix in ("", "-wal", "-shm"):
        path = Path(str(_DB_FILE) + suffix)
        if path.exists():
            path.unlink()

    SettingsService._cache = None  # 类变量缓存 —— 不清会串到上一个测试的库
    rate_limit.clear_all()
    preview_store.clear_all()

    with TestClient(app) as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin_token(client: TestClient) -> str:
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "be03-Admin-Pass", "role": "admin"},
    )
    body = resp.json()
    # 失败时把「当前生效的库」一起打出来 —— 混跑污染的症状是 20002，
    # 但根因通常在配置快照而不是密码，没有这行很难定位（见 main.py 的说明）
    assert body["code"] == 0, {
        "body": body,
        "db": str(get_settings().db_file),
        "user_count": _sql("SELECT COUNT(*) AS c FROM users")[0]["c"],
    }
    return body["data"]["access_token"]


def _new_student(client: TestClient, username: str = "20260001") -> tuple[str, int]:
    """注册一个学生，返回 `(token, user_id)`。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "real_name": "小明", "password": "student123"},
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["access_token"], body["data"]["user"]["id"]


def _make_unit(client: TestClient, token: str, name: str, **kw) -> int:
    payload = {
        "subject": kw.get("subject", "english"),
        "grade": kw.get("grade", 3),
        "semester": kw.get("semester", "first"),
        "name": name,
        "order_no": kw.get("order_no", 1),
    }
    resp = client.post("/api/units", json=payload, headers=_auth(token))
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["id"]


def _words_xlsx(rows: list[list]) -> bytes:
    return write_xlsx(WORDS_HEADERS, rows)


def _texts_xlsx(rows: list[list]) -> bytes:
    return write_xlsx(TEXTS_HEADERS, rows)


def _upload(client: TestClient, token: str, url: str, content: bytes, name: str = "a.xlsx") -> dict:
    resp = client.post(
        url,
        files={"file": (name, content, XLSX_MIME)},
        headers=_auth(token),
    )
    return resp.json()


def _word_row(
    en: str,
    cn: str,
    *,
    unit: str = "Unit 1 Hello!",
    grade: int = 3,
    type_: str = "单词",
    accepted: str = "",
    spell: str = "是",
    order: int = 0,
) -> list:
    """按 `WORDS_HEADERS` 的列序造一行。"""
    return [
        "英语",  # 学科
        grade,  # 年级
        "上学期",  # 学期
        unit,  # 单元名
        1,  # 单元序号
        type_,  # 类型
        en,  # 英文
        cn,  # 中文
        accepted,  # 可接受答案
        "",  # 音标
        "",  # 词性
        "",  # 例句英
        "",  # 例句中
        spell,  # 是否拼读
    ]


def _sql(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    with read_conn() as conn:
        return conn.execute(query, params).fetchall()


def _seed_answers(student_id: int, word_id: int, count: int, expected: str = "class") -> None:
    """给某词条造 `count` 条答题记录（U9 / U11 / U15 用）。"""
    with write_tx() as conn:
        cur = conn.execute(
            """
            INSERT INTO sessions (student_id, unit_id, content_type, strategy)
            VALUES (?, (SELECT unit_id FROM words WHERE id = ?), 'english', '{}')
            """,
            (student_id, word_id),
        )
        session_id = int(cur.lastrowid or 0)
        for i in range(count):
            conn.execute(
                """
                INSERT INTO answers (session_id, student_id, word_id, mode, step,
                                     expected, is_correct, match_type)
                VALUES (?, ?, ?, 'cn2en', 1, ?, 1, 'exact')
                """,
                (session_id, student_id, word_id, expected),
            )


def _seed_recitation(student_id: int, text_id: int) -> None:
    with write_tx() as conn:
        cur = conn.execute(
            """
            INSERT INTO sessions (student_id, unit_id, content_type, strategy)
            VALUES (?, (SELECT unit_id FROM texts WHERE id = ?), 'chinese', '{}')
            """,
            (student_id, text_id),
        )
        conn.execute(
            """
            INSERT INTO recitations (session_id, student_id, text_id, total_chars,
                                     matched_chars, covered_ratio, grade)
            VALUES (?, ?, ?, 10, 9, 0.9, '良好')
            """,
            (int(cur.lastrowid or 0), student_id, text_id),
        )


# ---------------------------------------------------------------------------
# F17 拼音服务（先测地基，后面导入的断言都建立在它之上）
# ---------------------------------------------------------------------------


def test_f17_pinyin_build_ok():
    """正常课文：plain 只留汉字、pinyin 逐字空格分隔、且严格等长。"""
    build = pinyin_service.build("我与父亲不相见已二年余了，我最不能忘记的是他的背影。")
    assert build.ok is True
    assert build.plain == "我与父亲不相见已二年余了我最不能忘记的是他的背影"
    assert len(build.tokens) == len(build.plain) == 24
    assert build.pinyin.startswith("wo yu fu qin")


def test_f17_han_only_strips_digits_and_latin():
    """`content_plain` 要**同时**去掉标点、数字、英文字母 —— 少去一样就会与拼音失配。"""
    assert pinyin_service.han_only("第3课 ABC 你好，世界！") == "第课你好世界"
    assert pinyin_service.count_han("abc 123 ，。") == 0


def test_f17_mismatch_on_rare_han():
    """生僻字触发 mismatch：pypinyin 返回原字符，不是拼音。

    这是 U7 的真实触发路径 —— 校验规则必须严于「等长」，否则这种坏数据会静默入库。
    """
    build = pinyin_service.build("他叫𠮷野家")
    assert build.ok is False
    assert build.plain == "他叫𠮷野家"
    assert build.mismatch_positions == [2]
    assert build.mismatches[0]["char"] == "𠮷"


def test_f17_check_length_mismatch():
    """真·长度不等也要能查出来（模拟 pypinyin 版本漂移导致 token 数变化）。"""
    ok, mism = pinyin_service.check("我们", "wo men men")
    assert ok is False
    assert "数量不一致" in mism[0]["reason"]


def test_f17_paragraph_split_and_scope():
    """段落按换行切、空行忽略；`build_scoped` 只拼背诵范围内的段。"""
    paras = pinyin_service.split_paragraphs("第一段\n\n第二段\r\n  \n第三段 ")
    assert paras == ["第一段", "第二段", "第三段"]
    assert pinyin_service.build_scoped(paras, [0, 2]).plain == "第一段第三段"
    assert pinyin_service.build_scoped(paras, []).plain == "第一段第二段第三段"


# ---------------------------------------------------------------------------
# U1 模板
# ---------------------------------------------------------------------------


def test_u1_words_template_has_14_columns_and_validation(client):
    """U1：words 模板 14 列 + 下拉有效性。"""
    from io import BytesIO

    from openpyxl import load_workbook

    token = _admin_token(client)
    resp = client.get("/api/import/template/words", headers=_auth(token))
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]

    wb = load_workbook(BytesIO(resp.content))
    ws = wb.active
    headers = [ws.cell(row=1, column=i + 1).value for i in range(len(WORDS_HEADERS))]
    assert headers == WORDS_HEADERS
    assert len(WORDS_HEADERS) == 14
    # 下拉有效性（学科 / 年级 / 学期 / 类型 / 是否拼读）
    assert len(ws.data_validations.dataValidation) >= 4


def test_u1_texts_template(client):
    """texts 模板 8 列，含「段落正文」与「背诵范围」。"""
    from io import BytesIO

    from openpyxl import load_workbook

    token = _admin_token(client)
    resp = client.get("/api/import/template/texts", headers=_auth(token))
    assert resp.status_code == 200

    wb = load_workbook(BytesIO(resp.content))
    ws = wb.active
    headers = [ws.cell(row=1, column=i + 1).value for i in range(len(TEXTS_HEADERS))]
    assert headers == TEXTS_HEADERS
    assert "段落正文" in headers and "背诵范围" in headers


# ---------------------------------------------------------------------------
# U2 词条导入预览（120 行 / 2 行非法）
# ---------------------------------------------------------------------------


def _build_120_rows() -> list[list]:
    """120 行：`Unit 1 Hello!`（已建）60 行 + `Unit 5`（未建）60 行。

    第 12 行年级=10、第 45 行类型=词组 —— 正好 2 行非法，对齐 U2 的 118 / 2。
    """
    rows: list[list] = []
    for i in range(120):
        unit = "Unit 1 Hello!" if i < 60 else "Unit 5"
        idx = i + 2  # Excel 行号
        grade = 10 if idx == 12 else 3
        type_ = "词组" if idx == 45 else "单词"
        rows.append(
            _word_row(
                f"word{i + 1:03d}",
                f"词{i + 1}",
                unit=unit,
                grade=grade,
                type_=type_,
                order=i,
            )
        )
    return rows


def test_u2_words_preview_counts_and_new_units(client):
    """U2：`valid=118` / `invalid=2`，错误行带原因，`new_units` 列出未建单元。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")

    data = _upload(client, token, "/api/import/words/preview", _words_xlsx(_build_120_rows()))
    assert data["code"] == 0, data
    summary = data["data"]["summary"]
    assert summary["total"] == 120
    assert summary["valid"] == 118
    assert summary["invalid"] == 2

    invalid = data["data"]["invalid_rows"]
    assert len(invalid) == 2
    rows_reported = sorted(i["row"] for i in invalid)
    assert rows_reported == [12, 45]
    reasons = " ".join(i["reason"] for i in invalid)
    assert "年级" in reasons and "10" in reasons
    assert "类型" in reasons and "词组" in reasons

    new_units = data["data"]["new_units"]
    assert [u["name"] for u in new_units] == ["Unit 5"]
    assert new_units[0]["grade"] == 3


# ---------------------------------------------------------------------------
# U3 / U4 / U5 commit 语义
# ---------------------------------------------------------------------------


def _commit_words(client, token, *, create_missing_units=True, on_duplicate="overwrite", rows=None):
    preview = _upload(
        client, token, "/api/import/words/preview", _words_xlsx(rows or _build_120_rows())
    )
    assert preview["code"] == 0, preview
    return client.post(
        "/api/import/words/commit",
        json={
            "preview_token": preview["data"]["preview_token"],
            "create_missing_units": create_missing_units,
            "on_duplicate": on_duplicate,
        },
        headers=_auth(token),
    ).json()


def test_u3_create_missing_units_false_rejects(client):
    """U3：不允许自动建单元时，**不建单元、不落词条**，且错误要说得清。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")

    body = _commit_words(client, token, create_missing_units=False)
    assert body["code"] == 40003, body
    assert body["data"]["reason"] == "create_missing_units_false"
    assert "Unit 5" in body["message"]

    # 一条都没落
    assert _sql("SELECT COUNT(*) AS c FROM words")[0]["c"] == 0
    assert _sql("SELECT COUNT(*) AS c FROM units")[0]["c"] == 1


def test_u4_second_commit_with_skip(client):
    """U4：首次全部新建；二次 `skip` 全部跳过，词条总数不变。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")

    first = _commit_words(client, token, on_duplicate="skip")
    assert first["code"] == 0, first
    assert first["data"]["created"] == 118
    assert first["data"]["units_created"] == 1
    total_before = _sql("SELECT COUNT(*) AS c FROM words")[0]["c"]
    assert total_before == 118

    second = _commit_words(client, token, on_duplicate="skip")
    assert second["data"]["created"] == 0
    assert second["data"]["updated"] == 0
    assert second["data"]["skipped"] == 118
    assert _sql("SELECT COUNT(*) AS c FROM words")[0]["c"] == total_before


def test_u5_overwrite_updates_cn_but_keeps_status(client):
    """U5：`overwrite` 更新 `cn`；**已停用的词条 `status` 仍为 inactive**。"""
    token = _admin_token(client)
    unit_id = _make_unit(client, token, "Unit 1 Hello!")

    rows = [_word_row("classroom", "教室"), _word_row("window", "窗户")]
    body = _commit_words(client, token, rows=rows)
    assert body["data"]["created"] == 2

    class_word = _sql("SELECT id FROM words WHERE en='classroom'")[0]
    window_word = _sql("SELECT id FROM words WHERE en='window'")[0]

    # 先把 window 停用
    resp = client.post(
        "/api/words/batch-status",
        json={"word_ids": [int(window_word["id"])], "status": "inactive"},
        headers=_auth(token),
    )
    assert resp.json()["data"]["updated"] == 1

    # 改 classroom 的中文，重新导入（overwrite）
    rows2 = [_word_row("classroom", "教室（改）"), _word_row("window", "窗户（改）")]
    body2 = _commit_words(client, token, rows=rows2, on_duplicate="overwrite")
    assert body2["data"]["updated"] == 2
    assert body2["data"]["created"] == 0

    assert _sql("SELECT cn FROM words WHERE id=?", (int(class_word["id"]),))[0]["cn"] == "教室（改）"
    # 关键：不覆盖 status
    assert (
        _sql("SELECT status FROM words WHERE id=?", (int(window_word["id"]),))[0]["status"]
        == "inactive"
    )
    assert unit_id > 0


# ---------------------------------------------------------------------------
# U6 / U7 / U8 课文导入
# ---------------------------------------------------------------------------

_BEIYING_PARAS = [
    "我与父亲不相见已二年余了，我最不能忘记的是他的背影。",
    "那年冬天，祖母死了，父亲的差使也交卸了，正是祸不单行的日子。",
    "我从北京到徐州，打算跟着父亲奔丧回家。",
]


def _text_row(
    paras: list[str],
    *,
    title: str = "背影",
    author: str = "朱自清",
    unit: str = "第一单元",
    scope: str = "",
) -> list:
    return ["语文", 7, "上学期", unit, title, author, "\n".join(paras), scope]


def test_u6_text_import_ok(client):
    """U6：《背影》`pinyin_check='ok'`；段落数正确；`total_chars` = 段落汉字数之和。"""
    token = _admin_token(client)
    data = _upload(client, token, "/api/import/texts/preview", _texts_xlsx([_text_row(_BEIYING_PARAS)]))
    assert data["code"] == 0, data

    item = data["data"]["texts"][0]
    assert item["pinyin_check"] == "ok"
    assert len(item["paragraphs"]) == 3
    expected_chars = sum(pinyin_service.count_han(p) for p in _BEIYING_PARAS)
    assert item["total_chars"] == expected_chars
    assert sum(p["chars"] for p in item["paragraphs"]) == expected_chars
    assert data["data"]["summary"]["valid"] == 1

    body = client.post(
        "/api/import/texts/commit",
        json={"preview_token": data["data"]["preview_token"], "texts": []},
        headers=_auth(token),
    ).json()
    assert body["code"] == 0, body
    assert body["data"]["created"] == 1

    text = _sql("SELECT * FROM texts")[0]
    assert text["recite_scope"] == "[0, 1, 2]"
    # 等长硬约束（契约 §4.5 第 1 条）
    assert len(text["content_plain"]) == len(text["content_pinyin"].split())
    assert len(text["content_plain"]) == expected_chars


def test_u7_pinyin_mismatch_rejected(client):
    """U7：正文含无法拼音化的字 → preview 标 mismatch 并给位置；commit **拒绝入库**。"""
    token = _admin_token(client)
    paras = ["他叫𠮷野家，住在村里。"]
    data = _upload(client, token, "/api/import/texts/preview", _texts_xlsx([_text_row(paras)]))
    assert data["code"] == 0, data

    item = data["data"]["texts"][0]
    assert item["pinyin_check"] == "mismatch"
    assert item["mismatch_positions"] == [2]
    # 位置要能在响应里定位到具体是哪个字
    issues = data["data"]["pinyin_issues"]
    assert issues and issues[0]["positions"] == [2]
    assert data["data"]["summary"]["invalid"] == 1
    assert data["data"]["summary"]["valid"] == 0

    body = client.post(
        "/api/import/texts/commit",
        json={"preview_token": data["data"]["preview_token"], "texts": []},
        headers=_auth(token),
    ).json()
    assert body["code"] == 40003, body
    assert body["data"]["reason"] == "pinyin_mismatch"
    assert _sql("SELECT COUNT(*) AS c FROM texts")[0]["c"] == 0


def test_u8_commit_applies_adjusted_scope(client):
    """U8：commit 回传 `recite_scope=[0,1]` → 库中为该值，且 `total_chars` 按新范围重算。"""
    token = _admin_token(client)
    preview = _upload(
        client, token, "/api/import/texts/preview", _texts_xlsx([_text_row(_BEIYING_PARAS)])
    )
    row_no = preview["data"]["texts"][0]["row"]

    body = client.post(
        "/api/import/texts/commit",
        json={
            "preview_token": preview["data"]["preview_token"],
            "texts": [{"row": row_no, "recite_scope": [0, 1]}],
        },
        headers=_auth(token),
    ).json()
    assert body["code"] == 0, body

    text = _sql("SELECT * FROM texts")[0]
    assert text["recite_scope"] == "[0, 1]"
    expected = pinyin_service.count_han(_BEIYING_PARAS[0]) + pinyin_service.count_han(
        _BEIYING_PARAS[1]
    )
    assert text["total_chars"] == expected
    assert len(text["content_plain"]) == expected
    assert "打算跟着父亲奔丧回家" not in text["content_plain"]

    import json

    paragraphs = json.loads(text["paragraphs"])
    assert [p["recite"] for p in paragraphs] == [True, True, False]


# ---------------------------------------------------------------------------
# U9 ~ U11 删除保护
# ---------------------------------------------------------------------------


def test_u9_delete_word_with_records_rejected(client):
    """U9：删除已有 37 条记录的词条 → `30003` + `record_count=37`。"""
    token = _admin_token(client)
    unit_id = _make_unit(client, token, "Unit 1 Hello!")
    _commit_words(client, token, rows=[_word_row("class", "班级")])

    _, student_id = _new_student(client)
    word_id = int(_sql("SELECT id FROM words")[0]["id"])
    _seed_answers(student_id, word_id, 37)

    body = client.delete(f"/api/words/{word_id}", headers=_auth(token)).json()
    assert body["code"] == 30003, body
    assert body["data"]["record_count"] == 37
    assert "停用" in body["message"]
    # 内容仍在
    assert _sql("SELECT COUNT(*) AS c FROM words")[0]["c"] == 1
    assert unit_id > 0


def test_u10_delete_word_without_records(client):
    """U10：无记录 → 物理删除成功。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")
    _commit_words(client, token, rows=[_word_row("class", "班级")])

    word_id = int(_sql("SELECT id FROM words")[0]["id"])
    body = client.delete(f"/api/words/{word_id}", headers=_auth(token)).json()
    assert body["code"] == 0, body
    assert body["data"]["deleted"] is True
    assert _sql("SELECT COUNT(*) AS c FROM words")[0]["c"] == 0


def test_u11_delete_unit_with_records_rejected(client):
    """U11：单元下任一内容有记录 → `30003`，拒绝删除。"""
    token = _admin_token(client)
    unit_id = _make_unit(client, token, "Unit 1 Hello!")
    _commit_words(client, token, rows=[_word_row("class", "班级"), _word_row("window", "窗户")])

    _, student_id = _new_student(client)
    word_id = int(_sql("SELECT id FROM words WHERE en='window'")[0]["id"])
    _seed_answers(student_id, word_id, 3)

    body = client.delete(f"/api/units/{unit_id}", headers=_auth(token)).json()
    assert body["code"] == 30003, body
    assert body["data"]["record_count"] == 3
    assert body["data"]["answer_count"] == 3
    assert _sql("SELECT COUNT(*) AS c FROM units")[0]["c"] == 1
    assert _sql("SELECT COUNT(*) AS c FROM words")[0]["c"] == 2


def test_u11b_delete_text_with_records_rejected(client):
    """课文删除保护（与单元同口径）。"""
    token = _admin_token(client)
    preview = _upload(
        client, token, "/api/import/texts/preview", _texts_xlsx([_text_row(_BEIYING_PARAS)])
    )
    client.post(
        "/api/import/texts/commit",
        json={"preview_token": preview["data"]["preview_token"], "texts": []},
        headers=_auth(token),
    )

    _, student_id = _new_student(client)
    text_id = int(_sql("SELECT id FROM texts")[0]["id"])
    _seed_recitation(student_id, text_id)

    body = client.delete(f"/api/texts/{text_id}", headers=_auth(token)).json()
    assert body["code"] == 30003, body
    assert body["data"]["record_count"] == 1
    assert _sql("SELECT COUNT(*) AS c FROM texts")[0]["c"] == 1


def test_delete_unit_without_records_cascades(client):
    """零记录 → 级联删掉单元下的词条。"""
    token = _admin_token(client)
    unit_id = _make_unit(client, token, "Unit 1 Hello!")
    _commit_words(client, token, rows=[_word_row("class", "班级"), _word_row("window", "窗户")])

    body = client.delete(f"/api/units/{unit_id}", headers=_auth(token)).json()
    assert body["code"] == 0, body
    assert body["data"]["deleted_words"] == 2
    assert _sql("SELECT COUNT(*) AS c FROM words")[0]["c"] == 0
    assert _sql("SELECT COUNT(*) AS c FROM units")[0]["c"] == 0


# ---------------------------------------------------------------------------
# U12 ~ U14 双角色词条列表
# ---------------------------------------------------------------------------


def _setup_words_for_role_tests(client) -> tuple[str, str, int, int]:
    """造一个单元 + 3 个词条（其中一个停用）。

    Returns:
        `(admin_token, student_token, student_id, unit_id)`
        —— 带上 `student_id`：直接给 `word_mastery` 造数据时要用**同一个**学生，
        用错人的 id 会让 `only=wrong` 永远查空，且看起来像功能坏了。
    """
    admin = _admin_token(client)
    unit_id = _make_unit(client, admin, "Unit 1 Hello!")
    _commit_words(
        client,
        admin,
        rows=[
            _word_row("classroom", "教室", accepted="课室"),
            _word_row("pencil", "铅笔"),
            _word_row("stop_it", "停用词"),
        ],
    )
    stopped = int(_sql("SELECT id FROM words WHERE en='stop_it'")[0]["id"])
    client.post(
        "/api/words/batch-status",
        json={"word_ids": [stopped], "status": "inactive"},
        headers=_auth(admin),
    )
    student_token, student_id = _new_student(client)
    return admin, student_token, student_id, unit_id


def test_u12_inactive_word_hidden_from_student(client):
    """U12：停用词条不出现在学生端。"""
    _, student, _, unit_id = _setup_words_for_role_tests(client)

    body = client.get(f"/api/units/{unit_id}/words", headers=_auth(student)).json()
    assert body["code"] == 0, body
    ens = [w["en"] for w in body["data"]["words"]]
    assert "stop_it" not in ens
    assert set(ens) == {"classroom", "pencil"}


def test_u13_student_view_hides_answer_side(client):
    """U13：student + `mode=cn2en` → 无 `en`、无 `accepted`，且带 `mastery.status`。"""
    _, student, _, unit_id = _setup_words_for_role_tests(client)

    body = client.get(
        f"/api/units/{unit_id}/words?mode=cn2en", headers=_auth(student)
    ).json()
    words = body["data"]["words"]
    assert words, body

    for w in words:
        assert w["en"] is None, "cn2en 模式必须隐藏英文，否则等于把答案给学生"
        assert w["cn"]
        assert "accepted" not in w, "accepted 是判分依据，绝不下发学生端"
        assert "status" not in w
        assert w["mastery"]["status"] in (
            "not_started",
            "practiced_with_error",
            "practiced_ok",
            "mastered",
        )
    assert all(w["mastery"]["status"] == "not_started" for w in words)


def test_u13b_student_mode_en2cn_hides_cn(client):
    """`mode=en2cn` 反向：隐藏中文、给出英文。"""
    _, student, _, unit_id = _setup_words_for_role_tests(client)
    body = client.get(
        f"/api/units/{unit_id}/words?mode=en2cn", headers=_auth(student)
    ).json()
    for w in body["data"]["words"]:
        assert w["cn"] is None
        assert w["en"]
        assert "accepted" not in w


def test_u14_admin_view_returns_all_fields(client):
    """U14：admin 拉同一路径 → 全字段，含 `accepted` 与 `status`，并分页。"""
    admin, _, _, unit_id = _setup_words_for_role_tests(client)

    body = client.get(f"/api/units/{unit_id}/words", headers=_auth(admin)).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["total"] == 3, "管理端默认要看到停用词条，否则无法恢复"
    assert data["unit_name"] == "Unit 1 Hello!"
    assert data["subject"] == "english"

    by_en = {w["en"]: w for w in data["items"]}
    classroom = by_en["classroom"]
    assert classroom["accepted"] == ["课室"]
    assert classroom["status"] == "active"
    assert classroom["require_spell"] is True, "契约 §4.6：API 层布尔必须是 true/false"
    assert by_en["stop_it"]["status"] == "inactive"


def test_admin_word_filters(client):
    """管理端 `keyword` / `status` 筛选。"""
    admin, _, _, unit_id = _setup_words_for_role_tests(client)

    body = client.get(
        f"/api/units/{unit_id}/words?keyword=class", headers=_auth(admin)
    ).json()
    assert [w["en"] for w in body["data"]["items"]] == ["classroom"]

    body = client.get(
        f"/api/units/{unit_id}/words?status=inactive", headers=_auth(admin)
    ).json()
    assert [w["en"] for w in body["data"]["items"]] == ["stop_it"]

    # 中文关键词也能搜
    body = client.get(f"/api/units/{unit_id}/words?keyword=铅笔", headers=_auth(admin)).json()
    assert [w["en"] for w in body["data"]["items"]] == ["pencil"]


def test_student_only_filters(client):
    """学生端 `only=wrong` / `only=unmastered`。"""
    admin, student, student_id, unit_id = _setup_words_for_role_tests(client)
    word_id = int(_sql("SELECT id FROM words WHERE en='classroom'")[0]["id"])

    with write_tx() as conn:
        conn.execute(
            """
            INSERT INTO word_mastery (student_id, word_id, streak, total_count,
                                      correct_count, wrong_count)
            VALUES (?, ?, 0, 3, 1, 2)
            """,
            (student_id, word_id),
        )

    body = client.get(
        f"/api/units/{unit_id}/words?only=wrong", headers=_auth(student)
    ).json()
    assert [w["cn"] for w in body["data"]["words"]] == ["教室"]

    body = client.get(
        f"/api/units/{unit_id}/words?only=unmastered", headers=_auth(student)
    ).json()
    assert len(body["data"]["words"]) == 2, "练过有错 + 没练过的都算未掌握"

    # 掌握一个之后应从 unmastered 里消失
    with write_tx() as conn:
        conn.execute(
            "UPDATE word_mastery SET mastered_at = '2026-09-24T00:00:00Z', wrong_count = 0 "
            "WHERE student_id = ? AND word_id = ?",
            (student_id, word_id),
        )
    body = client.get(
        f"/api/units/{unit_id}/words?only=unmastered", headers=_auth(student)
    ).json()
    assert [w["cn"] for w in body["data"]["words"]] == ["铅笔"]


# ---------------------------------------------------------------------------
# U15 改词条不影响历史记录
# ---------------------------------------------------------------------------


def test_u15_patch_word_keeps_history_snapshot(client):
    """U15：改 `cn` 后历史 `expected` 仍是**修改前的快照**，且响应带 `history_affected`。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")
    _commit_words(client, token, rows=[_word_row("class", "班级")])

    _, student_id = _new_student(client)
    word_id = int(_sql("SELECT id FROM words")[0]["id"])
    _seed_answers(student_id, word_id, 2, expected="班级")

    body = client.patch(
        f"/api/words/{word_id}", json={"cn": "班级（改）"}, headers=_auth(token)
    ).json()
    assert body["code"] == 0, body
    assert body["data"]["cn"] == "班级（改）"
    assert body["data"]["history_affected"] is True

    expected_values = {r["expected"] for r in _sql("SELECT expected FROM answers")}
    assert expected_values == {"班级"}, "历史记录是学生当时看到的题，改词条绝不能回写"

    # 无记录时 history_affected 为 False
    other = _sql("SELECT id FROM words")[0]
    assert other["id"] == word_id


def test_patch_word_duplicate_en_rejected(client):
    """同单元内改英文撞车 → `10001`。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")
    _commit_words(client, token, rows=[_word_row("class", "班级"), _word_row("window", "窗户")])

    word_id = int(_sql("SELECT id FROM words WHERE en='window'")[0]["id"])
    body = client.patch(
        f"/api/words/{word_id}", json={"en": "class"}, headers=_auth(token)
    ).json()
    assert body["code"] == 10001, body
    assert "class" in body["message"]


def test_batch_status_returns_affected_count(client):
    """批量停用返回实际改动条数（不存在的 id 不计入）。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")
    _commit_words(client, token, rows=[_word_row("class", "班级"), _word_row("window", "窗户")])
    ids = [int(r["id"]) for r in _sql("SELECT id FROM words")]

    body = client.post(
        "/api/words/batch-status",
        json={"word_ids": [*ids, 999999], "status": "inactive"},
        headers=_auth(token),
    ).json()
    assert body["data"]["updated"] == 2
    assert body["data"]["requested"] == 3


# ---------------------------------------------------------------------------
# U16 ~ U19 导入异常与单元重复
# ---------------------------------------------------------------------------


def test_u16_non_xlsx_rejected(client):
    """U16：非 xlsx → `40001`。"""
    token = _admin_token(client)
    data = _upload(client, token, "/api/import/words/preview", b"this is not an excel file")
    assert data["code"] == 40001, data


def test_u17_missing_columns_rejected(client):
    """U17：缺列 → `40002`，提示下载最新模板。"""
    token = _admin_token(client)
    content = write_xlsx(["学科", "英文"], [["英语", "class"]])
    data = _upload(client, token, "/api/import/words/preview", content)
    assert data["code"] == 40002, data
    assert "下载最新模板" in data["message"]
    assert "年级" in data["data"]["missing_columns"]


def test_u18_file_too_large(client):
    """U18：11MB → `40004`（HTTP 413，契约 §5.1）。"""
    token = _admin_token(client)
    resp = client.post(
        "/api/import/words/preview",
        files={"file": ("big.xlsx", b"x" * (11 * 1024 * 1024), XLSX_MIME)},
        headers=_auth(token),
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == 40004


def test_u19_duplicate_unit_name(client):
    """U19：同名单元 → `10001`，提示已存在。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")

    body = client.post(
        "/api/units",
        json={"subject": "english", "grade": 3, "semester": "first", "name": "Unit 1 Hello!"},
        headers=_auth(token),
    ).json()
    assert body["code"] == 10001, body
    assert "Unit 1 Hello!" in body["message"]

    # 不同年级同名可以建（唯一键含 subject/grade/semester）
    body2 = client.post(
        "/api/units",
        json={"subject": "english", "grade": 4, "semester": "first", "name": "Unit 1 Hello!"},
        headers=_auth(token),
    ).json()
    assert body2["code"] == 0, body2


# ---------------------------------------------------------------------------
# 单元列表与课文接口
# ---------------------------------------------------------------------------


def test_unit_list_derived_counts(client):
    """F1：`GET /api/units` 的 4 个派生计数 + 筛选。"""
    token = _admin_token(client)
    unit_id = _make_unit(client, token, "Unit 1 Hello!")
    _commit_words(client, token, rows=[_word_row("class", "班级"), _word_row("window", "窗户")])
    stopped = int(_sql("SELECT id FROM words WHERE en='window'")[0]["id"])
    client.post(
        "/api/words/batch-status",
        json={"word_ids": [stopped], "status": "inactive"},
        headers=_auth(token),
    )
    _make_unit(client, token, "二年级单元", grade=2)

    data = client.get("/api/units?subject=english&grade=3", headers=_auth(token)).json()
    assert data["data"]["total"] == 1
    item = data["data"]["items"][0]
    assert item["word_count"] == 2
    assert item["active_word_count"] == 1
    assert item["text_count"] == 0
    assert item["active_text_count"] == 0
    assert item["id"] == unit_id

    # 不带筛选时两个单元都在
    assert client.get("/api/units", headers=_auth(token)).json()["data"]["total"] == 2


def test_text_list_and_detail_no_pinyin(client):
    """F9 / F10：列表不含正文；详情含 paragraphs + scope_snapshot，**不含拼音**。"""
    token = _admin_token(client)
    preview = _upload(
        client, token, "/api/import/texts/preview", _texts_xlsx([_text_row(_BEIYING_PARAS)])
    )
    client.post(
        "/api/import/texts/commit",
        json={"preview_token": preview["data"]["preview_token"], "texts": []},
        headers=_auth(token),
    )
    unit_id = int(_sql("SELECT unit_id FROM texts")[0]["unit_id"])
    text_id = int(_sql("SELECT id FROM texts")[0]["id"])

    listing = client.get(f"/api/units/{unit_id}/texts", headers=_auth(token)).json()
    assert listing["data"]["unit_name"] == "第一单元"
    brief = listing["data"]["texts"][0]
    assert "content_raw" not in brief, "列表不该带正文"
    assert brief["paragraph_count"] == 3
    assert brief["recite_paragraph_count"] == 3

    detail = client.get(f"/api/texts/{text_id}", headers=_auth(token)).json()["data"]
    assert len(detail["paragraphs"]) == 3
    assert detail["recite_scope"] == [0, 1, 2]
    assert detail["scope_snapshot"] == {
        "start": 0,
        "end": sum(pinyin_service.count_han(p) for p in _BEIYING_PARAS),
    }
    assert "content_pinyin" not in detail, "§8.7 明文：不下发拼音序列"
    assert "content_plain" not in detail


def test_text_scope_snapshot_partial(client):
    """`scope_snapshot` 是 `[start, end)` 半开区间，仅覆盖背诵范围。"""
    token = _admin_token(client)
    preview = _upload(
        client, token, "/api/import/texts/preview", _texts_xlsx([_text_row(_BEIYING_PARAS)])
    )
    row_no = preview["data"]["texts"][0]["row"]
    client.post(
        "/api/import/texts/commit",
        json={
            "preview_token": preview["data"]["preview_token"],
            "texts": [{"row": row_no, "recite_scope": [1]}],
        },
        headers=_auth(token),
    )
    text_id = int(_sql("SELECT id FROM texts")[0]["id"])
    detail = client.get(f"/api/texts/{text_id}", headers=_auth(token)).json()["data"]

    first = pinyin_service.count_han(_BEIYING_PARAS[0])
    second = pinyin_service.count_han(_BEIYING_PARAS[1])
    assert detail["scope_snapshot"] == {"start": first, "end": first + second}
    assert detail["recite_scope"] == [1]


def test_student_text_list_hides_management_fields(client):
    """学生端课文列表：只有 active，且不带 status / order_no。"""
    admin = _admin_token(client)
    preview = _upload(
        client, admin, "/api/import/texts/preview", _texts_xlsx([_text_row(_BEIYING_PARAS)])
    )
    client.post(
        "/api/import/texts/commit",
        json={"preview_token": preview["data"]["preview_token"], "texts": []},
        headers=_auth(admin),
    )
    unit_id = int(_sql("SELECT unit_id FROM texts")[0]["unit_id"])
    student, _ = _new_student(client)

    body = client.get(f"/api/units/{unit_id}/texts", headers=_auth(student)).json()
    brief = body["data"]["texts"][0]
    assert brief["title"] == "背影"
    assert "status" not in brief and "order_no" not in brief
    assert brief["last_grade"] is None


# ---------------------------------------------------------------------------
# F16 错误报告（与 BE-02 共用路径）
# ---------------------------------------------------------------------------


def test_error_report_shared_path_for_words(client):
    """词条导入的错误报告能通过**与学生名单同一个路径**下载，且表头是 14 列。"""
    from io import BytesIO

    from openpyxl import load_workbook

    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")
    data = _upload(client, token, "/api/import/words/preview", _words_xlsx(_build_120_rows()))
    url = data["data"]["error_report_url"]
    assert url.startswith("/api/import/report/")

    resp = client.get(url, headers=_auth(token))
    assert resp.status_code == 200
    wb = load_workbook(BytesIO(resp.content))
    ws = wb.active
    headers = [c.value for c in ws[1]]
    assert headers == ["原文件行号", "错误原因", *WORDS_HEADERS]
    assert ws.max_row == 3  # 表头 + 2 行错误


def test_error_report_expired_token(client):
    """过期 token → `40003`，而不是 500。"""
    token = _admin_token(client)
    body = client.get("/api/import/report/not-a-real-token", headers=_auth(token)).json()
    assert body["code"] == 40003, body


def test_students_error_report_still_works(client):
    """改造后学生名单的错误报告**行为不变**（回归保护）。

    ⚠️ 这条是**跨包回归**：本轮把 `/api/import/report/{token}` 的 handler
    改成了「按 kind 分派」，BE-02 的名单导入必须照旧能下载到 6 列表头的报告。
    """
    from io import BytesIO

    from openpyxl import load_workbook

    token = _admin_token(client)
    content = write_xlsx(
        ["学号", "姓名", "班级"],
        [
            ["20260001", "小明", ""],
            ["", "阿", ""],  # 学号为空
            ["20260003", "", ""],  # 姓名为空
        ],
    )
    resp = client.post(
        "/api/students/import",
        files={"file": ("s.xlsx", content, XLSX_MIME)},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["summary"]["invalid"] == 2, body["data"]

    report = client.get(body["data"]["error_report_url"], headers=_auth(token))
    wb = load_workbook(BytesIO(report.content))
    assert [c.value for c in wb.active[1]] == [
        "原文件行号",
        "错误原因",
        "学号",
        "姓名",
        "班级",
        "初始密码",
    ]


# ---------------------------------------------------------------------------
# 权限与参数校验
# ---------------------------------------------------------------------------


def test_admin_only_endpoints(client):
    """内容管理与导入接口全部要求 admin（学生访问 → `10003`）。"""
    student, _ = _new_student(client)
    checks = [
        ("get", "/api/units", {}),
        ("get", "/api/import/template/words", {}),
        ("post", "/api/words/batch-status", {"json": {"word_ids": [1], "status": "inactive"}}),
        ("post", "/api/units", {"json": {"subject": "english", "grade": 3,
                                         "semester": "first", "name": "X"}}),
    ]
    for method, url, kwargs in checks:
        resp = getattr(client, method)(url, headers=_auth(student), **kwargs)
        assert resp.json()["code"] == 10003, (method, url, resp.json())

    # 未登录 → 10002
    assert client.get("/api/units").json()["code"] == 10002
    # 学生可以用双角色路径（不是 10003），只是被裁剪
    assert client.get("/api/units/1/words", headers=_auth(student)).json()["code"] == 30001


def test_preview_token_is_single_use(client):
    """token 一次性：重复 commit 第二次报「已失效」，防止重复入库。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")
    preview = _upload(client, token, "/api/import/words/preview", _words_xlsx([_word_row("a", "甲")]))
    pv = preview["data"]["preview_token"]

    first = client.post(
        "/api/import/words/commit",
        json={"preview_token": pv, "create_missing_units": True, "on_duplicate": "overwrite"},
        headers=_auth(token),
    ).json()
    assert first["code"] == 0, first

    second = client.post(
        "/api/import/words/commit",
        json={"preview_token": pv, "create_missing_units": True, "on_duplicate": "overwrite"},
        headers=_auth(token),
    ).json()
    assert second["code"] == 40003, second
    assert second["data"]["reason"] == "preview_token_expired"
    assert _sql("SELECT COUNT(*) AS c FROM words")[0]["c"] == 1


def test_import_validation_details(client):
    """逐行校验的细节：全角数字、非法枚举、空必填、文件内重复。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")
    rows = [
        _word_row("a", "甲"),
        _word_row("b", "乙", grade=0),  # 年级越界
        _word_row("c", "丙", type_="phrase_typo"),  # 类型非法
        _word_row("", "无英文"),  # 英文必填
        _word_row("a", "重复英文"),  # 文件内 (单元, 英文) 重复
        ["", 3, "上学期", "Unit 1 Hello!", 1, "单词", "d", "丁", "", "", "", "", "", "是"],  # 学科空
    ]
    data = _upload(client, token, "/api/import/words/preview", _words_xlsx(rows))
    summary = data["data"]["summary"]
    assert summary["total"] == 6
    assert summary["valid"] == 1
    assert summary["invalid"] == 5

    reasons = " ".join(i["reason"] for i in data["data"]["invalid_rows"])
    assert "年级" in reasons
    assert "类型" in reasons
    assert "英文不能为空" in reasons
    assert "重复" in reasons
    assert "学科" in reasons


def test_import_accepts_english_enums_and_duplicated_count(client):
    """枚举收英文写法；`duplicated` 统计**库中已存在**的行数（不计入 invalid）。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")
    rows = [
        ["english", 3, "first", "Unit 1 Hello!", 1, "word", "class", "班级", "", "", "", "", "", "1"],
        ["english", 3, "first", "Unit 1 Hello!", 1, "word", "window", "窗户", "", "", "", "", "", "1"],
    ]
    data = _upload(client, token, "/api/import/words/preview", _words_xlsx(rows))
    assert data["data"]["summary"]["valid"] == 2
    assert data["data"]["summary"]["duplicated"] == 0

    client.post(
        "/api/import/words/commit",
        json={
            "preview_token": data["data"]["preview_token"],
            "create_missing_units": True,
            "on_duplicate": "overwrite",
        },
        headers=_auth(token),
    )

    again = _upload(client, token, "/api/import/words/preview", _words_xlsx(rows))
    assert again["data"]["summary"]["duplicated"] == 2
    assert again["data"]["summary"]["invalid"] == 0
    assert again["data"]["summary"]["valid"] == 2


def test_acceptable_answers_roundtrip(client):
    """`accepted` 入库为 JSON 字符串、出参为数组；分隔符多种写法都认。"""
    token = _admin_token(client)
    unit_id = _make_unit(client, token, "Unit 1 Hello!")
    rows = [_word_row("classroom", "教室", accepted="课室|教室2")]
    _commit_words(client, token, rows=rows)

    stored = _sql("SELECT accepted FROM words")[0]["accepted"]
    assert stored == '["课室", "教室2"]'

    body = client.get(f"/api/units/{unit_id}/words", headers=_auth(token)).json()
    assert body["data"]["items"][0]["accepted"] == ["课室", "教室2"]

    # 改成别的分隔符也应识别
    data = _upload(
        client, token, "/api/import/words/preview", _words_xlsx([_word_row("x", "甲", accepted="一、二、三")])
    )
    assert data["code"] == 0, data


def test_require_spell_default_by_type(client):
    """`require_spell` 默认值：单词=是、短语/句子=否（数据库设计 §3.4 建议）。"""
    token = _admin_token(client)
    _make_unit(client, token, "Unit 1 Hello!")
    rows = [
        _word_row("class", "班级", spell=""),  # 空 → 按类型默认
        _word_row("in the end", "最后", type_="短语", spell=""),
        _word_row("how are you", "你好吗", type_="句子", spell=""),
    ]
    _commit_words(client, token, rows=rows)

    stored = {r["en"]: r["require_spell"] for r in _sql("SELECT en, require_spell FROM words")}
    assert stored["class"] == 1
    assert stored["in the end"] == 0
    assert stored["how are you"] == 0
