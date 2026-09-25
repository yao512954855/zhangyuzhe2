"""WP-BE-07 统计看板与申诉复核 —— 验收测试（10b U1 ~ U13）。

数据是**直接 SQL 造**的，不走接口：42 人的 7 天答题记录走一遍 HTTP 会跑成分钟级，
而本模块要验的是**聚合口径**，不是写入链路（写入链路由 BE-03/BE-04 的测试覆盖）。

三个班各司其职：
- **五(1)班**（42 人，grade 5）：U1 班级概览、U2 单元掌握度；
- **六(1)班**（1 人，grade 6）：U4 正确率公式、U5 跨接口一致性、U11 北京时间跨日；
  单独开一个班是为了让「班级 = 单元 = 学生」三个口径的样本集合**完全相同**，
  否则 U5 的三个数字天然不等，测了也说明不了口径是否一致；
- 语文单元：U3 背诵记录要评级。
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be07_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "test-secret-" + "0" * 32
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "CHANGE_ME_admin_password"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.main import app  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402
from app.services.stats_service import StatsService  # noqa: E402
from app.services.stats_service import beijing_day as _system_today  # noqa: E402
from app.services.user_service import UserService  # noqa: E402
from app.tasks import asr_daily_rollup  # noqa: E402


# =========================================================================== 夹具


@pytest.fixture(autouse=True)
def _no_rate_limit():
    rate_limit.clear_all()
    yield


@pytest.fixture(scope="module", autouse=True)
def _fresh_config():
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "test-secret-" + "0" * 32
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "CHANGE_ME_admin_password"
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    return _DB_FILE


@pytest.fixture(scope="module")
def client(_fresh_config):
    with TestClient(app, raise_server_exceptions=False) as c:
        SettingsService.refresh()
        yield c


# =========================================================================== 造数


def _utc(bj_day: str, hour: int = 12, minute: int = 0) -> str:
    """给定**北京时间**的某天某时，返回入库用的 UTC ISO 串。

    U11 就靠这个：北京时间 07:30 对应 UTC 23:30（前一天），
    两种切天方式的结论在这里必然分叉。
    """
    dt = datetime.fromisoformat(f"{bj_day}T{hour:02d}:{minute:02d}:00+08:00")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Seed:
    """造数结果（测试里到处要用这些 id）。"""

    #: 造数那一刻的**北京时间今天**。全模块的「今天/昨天」都以它为基准 ——
    #: 直接用 `beijing_day(0)` 会在套件恰好跨过北京零点时前后不一致
    #: （造数用 09-24、断言用 09-25），表现为随机的“趋势少一天”类假失败。
    today: str = ""

    class_a: int = 0
    class_b: int = 0
    unit_a: int = 0
    unit_b: int = 0
    unit_zh: int = 0
    text_zh: int = 0
    words_a: list[int] = []
    words_b: list[int] = []
    students_a: list[int] = []
    student_b: int = 0

    #: 五(1)班整体正确率的手工预期（U1 用它对齐）
    expected_accuracy: float = 0.0
    expected_counted: int = 0
    expected_correct: int = 0


SEED = Seed()


def beijing_day(offset_days: int = 0) -> str:
    """本模块的「北京时间某天」—— 锚定到**造数时**的今天，不随运行时刻漂移。"""
    base = SEED.today or _system_today(0)
    return (date.fromisoformat(base) + timedelta(days=offset_days)).isoformat()


_MAIN_PATTERN_EVEN = ["exact", "exact", "loose", "wrong", "empty"]
_MAIN_PATTERN_ODD = ["exact", "fuzzy", "wrong", "wrong", "empty"]


def _seed_all() -> None:
    SEED.today = _system_today(0)
    today = SEED.today

    with write_tx() as conn:
        # ---- 班级 ----
        cur = conn.execute(
            "INSERT INTO classes (name, grade, semester, school_year) VALUES (?,?,?,?)",
            ("五(1)班", 5, "first", "2026-2027"),
        )
        SEED.class_a = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO classes (name, grade, semester, school_year) VALUES (?,?,?,?)",
            ("六(1)班", 6, "first", "2026-2027"),
        )
        SEED.class_b = int(cur.lastrowid)

        # ---- 单元 ----
        cur = conn.execute(
            "INSERT INTO units (subject, grade, semester, name, order_no) VALUES (?,?,?,?,?)",
            ("english", 5, "first", "Unit 1 Hello", 1),
        )
        SEED.unit_a = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO units (subject, grade, semester, name, order_no) VALUES (?,?,?,?,?)",
            ("english", 6, "first", "Unit 1 六", 1),
        )
        SEED.unit_b = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO units (subject, grade, semester, name, order_no) VALUES (?,?,?,?,?)",
            ("chinese", 5, "first", "第一单元 古诗", 1),
        )
        SEED.unit_zh = int(cur.lastrowid)

        # ---- 词条 ----
        for i in range(10):
            cur = conn.execute(
                "INSERT INTO words (unit_id, type, en, cn, order_no) VALUES (?,?,?,?,?)",
                (SEED.unit_a, "word", f"word{i:02d}", f"词{i:02d}", i),
            )
            SEED.words_a.append(int(cur.lastrowid))
        for i in range(10):
            cur = conn.execute(
                "INSERT INTO words (unit_id, type, en, cn, order_no) VALUES (?,?,?,?,?)",
                (SEED.unit_b, "word", f"six{i:02d}", f"六{i:02d}", i),
            )
            SEED.words_b.append(int(cur.lastrowid))

        # ---- 语文课文 ----
        cur = conn.execute(
            """
            INSERT INTO texts (unit_id, title, author, content_raw, content_plain,
                               content_pinyin, paragraphs, recite_scope, total_chars)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (SEED.unit_zh, "静夜思", "李白", "床前明月光", "床 前 明 月 光",
             "chuang qian ming yue guang", "[]", "all", 5),
        )
        SEED.text_zh = int(cur.lastrowid)

        # ---- 学生 ----
        for i in range(42):
            cur = conn.execute(
                """
                INSERT INTO users (role, username, real_name, password_hash,
                                   class_id, register_source, status)
                VALUES ('student', ?, ?, 'x', ?, 'import', 'active')
                """,
                (f"a{i:03d}", f"甲班学生{i:02d}", SEED.class_a),
            )
            SEED.students_a.append(int(cur.lastrowid))

        cur = conn.execute(
            """
            INSERT INTO users (role, username, real_name, password_hash,
                               class_id, register_source, status)
            VALUES ('student', ?, ?, 'x', ?, 'import', 'active')
            """,
            ("b001", "乙班学生", SEED.class_b),
        )
        SEED.student_b = int(cur.lastrowid)

        # ---- 五(1)班：42 人 × 7 天，每天 1 场 5 题 ----
        total_correct = 0
        total_counted = 0
        for i, sid in enumerate(SEED.students_a):
            day = beijing_day(-(i % 7))
            pattern = _MAIN_PATTERN_EVEN if i % 2 == 0 else _MAIN_PATTERN_ODD
            correct = sum(1 for m in pattern if m in ("exact", "loose", "fuzzy"))
            counted = sum(1 for m in pattern if m != "empty")
            total_correct += correct
            total_counted += counted

            cur = conn.execute(
                """
                INSERT INTO sessions (student_id, unit_id, content_type, strategy,
                                      total_items, correct_items, status, started_at, ended_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (sid, SEED.unit_a, "english", "sequential", 5, correct, "finished",
                 _utc(day, 10, 0), _utc(day, 10, 3)),
            )
            session_id = int(cur.lastrowid)

            for w_index, match_type in enumerate(pattern):
                conn.execute(
                    """
                    INSERT INTO answers (session_id, student_id, word_id, mode, step,
                                         expected, asr_text, is_correct, match_type,
                                         duration_ms, retry_count, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        session_id,
                        sid,
                        SEED.words_a[w_index],
                        "cn2en",
                        1,
                        f"word{w_index:02d}",
                        "" if match_type == "empty" else "heard",
                        1 if match_type in ("exact", "loose", "fuzzy") else 0,
                        match_type,
                        3000 + i * 10,
                        0,
                        _utc(day, 10, w_index),
                    ),
                )
                # ⚠️ `empty` **不建** `word_mastery` 行 —— 这是 BE-04 的真实行为
                #   （`MasteryService.update_after_answer` 对 empty 明文忽略，见其
                #   docstring 第 16 行）。造数必须照抄这条规则，否则
                #   `v_student_unit_progress.word_practiced` 会比真实情况多 1，
                #   同一个页面里就会冒出两个不同的「练过词数」。
                if match_type == "empty":
                    continue
                w = SEED.words_a[w_index]
                conn.execute(
                    """
                    INSERT INTO word_mastery (student_id, word_id, streak, total_count,
                                              correct_count, wrong_count, last_result,
                                              last_answer_at, mastered_at, avg_duration_ms)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(student_id, word_id) DO UPDATE SET
                        total_count = total_count + 1,
                        correct_count = correct_count + excluded.correct_count,
                        wrong_count = wrong_count + excluded.wrong_count
                    """,
                    (
                        sid,
                        w,
                        1,
                        1,
                        1 if match_type in ("exact", "loose", "fuzzy") else 0,
                        0 if match_type in ("exact", "loose", "fuzzy") else 1,
                        1 if match_type in ("exact", "loose", "fuzzy") else 0,
                        _utc(day, 10, w_index),
                        # 前两个词「已掌握」，其余练过但没掌握
                        # → 未完成名单里的人各缺 6 个词（10 − 4）
                        _utc(day, 10, w_index) if w_index < 2 and i % 2 == 0 else None,
                        3000 + i * 10,
                    ),
                )

        SEED.expected_correct = total_correct
        SEED.expected_counted = total_counted
        SEED.expected_accuracy = StatsService.calc_accuracy(total_correct, total_counted)

        # ---- 六(1)班：2 exact + 1 wrong + 1 empty → 正确率 2/3 ----
        cur = conn.execute(
            """
            INSERT INTO sessions (student_id, unit_id, content_type, strategy,
                                  total_items, correct_items, status, started_at, ended_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (SEED.student_b, SEED.unit_b, "english", "sequential", 4, 2, "finished",
             _utc(today, 7, 30), _utc(today, 7, 33)),
        )
        session_b = int(cur.lastrowid)
        # ⚠️ 07:30 北京时间 = 前一天 23:30 UTC —— U11 的样本
        for w_index, match_type in enumerate(["exact", "exact", "wrong", "empty"]):
            conn.execute(
                """
                INSERT INTO answers (session_id, student_id, word_id, mode, step,
                                     expected, asr_text, is_correct, match_type,
                                     duration_ms, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_b,
                    SEED.student_b,
                    SEED.words_b[w_index],
                    "cn2en",
                    1,
                    f"six{w_index:02d}",
                    "" if match_type == "empty" else "heard",
                    1 if match_type == "exact" else 0,
                    match_type,
                    2000,
                    _utc(today, 7, 30 + w_index),
                ),
            )

        # ---- 背诵记录（U3 要评级） ----
        cur = conn.execute(
            """
            INSERT INTO sessions (student_id, unit_id, content_type, strategy,
                                  total_items, correct_items, status, score)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (SEED.students_a[0], SEED.unit_zh, "chinese", "recite", 1, 1, "finished", 92.5),
        )
        session_zh = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO recitations (session_id, student_id, text_id, transcript,
                                     total_chars, matched_chars, covered_ratio, score,
                                     grade, duration_ms, first_voice_ms, finish_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (session_zh, SEED.students_a[0], SEED.text_zh, "床前明月光",
             5, 5, 1.0, 92.5, "优秀", 12000, 800, "completed"),
        )

        # ---- 一条申诉样本（U6/U7/U8） ----
        conn.execute(
            "UPDATE answers SET appealed = 1 WHERE student_id = ? AND match_type = 'wrong'",
            (SEED.student_b,),
        )


@pytest.fixture(scope="module", autouse=True)
def _seeded(_fresh_config, client):
    """造数在 client 起来之后做 —— 否则 `bootstrap()` 会先建库，
    而我们要往已经建好的库里写。

    ⚠️ 注意 `lifespan` 启动时会跑一次日任务：北京时间已过 02:00/03:00 的话，
    定时备份与 ASR 聚合会立刻执行一次。此时库里还没有 asr_logs，聚合是空跑 ——
    对下面的用例没有影响，但**不要在造数之前依赖「asr_logs 是空的」**。
    """
    _seed_all()
    # 把调度器的日任务标记为「今天已跑」，避免心跳（60s）在长测试中途再触发一次
    from app.tasks import scheduler

    for job in scheduler.jobs():
        job.last_run_date = beijing_day(0)
    yield


# =========================================================================== 工具


def _admin_headers(client) -> dict:
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "CHANGE_ME_admin_password", "role": "admin"},
    ).json()
    assert resp.get("code") == 0, f"admin 登录失败：{resp}"
    return {"Authorization": f"Bearer {resp['data']['access_token']}"}


def _audits(action: str) -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT detail FROM audit_logs WHERE action = ? ORDER BY id DESC", (action,)
        ).fetchall()
    return [json.loads(r["detail"]) for r in rows]


# =========================================================================== U1


def test_u1_class_overview(client):
    """U1：42 人、7 天数据 → 各字段数值正确、趋势 7 个点、错题按次数降序。"""
    body = client.get(
        f"/api/stats/class/{SEED.class_a}", headers=_admin_headers(client)
    ).json()
    assert body["code"] == 0, body
    d = body["data"]

    assert d["student_count"] == 42
    assert d["active_student_count"] == 42
    assert d["accuracy"] == pytest.approx(SEED.expected_accuracy), (
        f"班级正确率应与手工值一致：{d['accuracy']} vs {SEED.expected_accuracy}"
    )
    assert d["counted_count"] == SEED.expected_counted
    assert d["correct_count"] == SEED.expected_correct
    # empty 不计入分母：42 人各有 1 条 empty
    assert d["answer_count"] == SEED.expected_counted + 42

    assert len(d["trend"]) == 7, f"默认近 7 天应有 7 个点，实际 {len(d['trend'])}"
    assert [p["date"] for p in d["trend"]] == sorted(p["date"] for p in d["trend"]), "趋势应升序"
    assert sum(p["item_count"] for p in d["trend"]) == d["answer_count"]

    # 词 3 被两种 pattern 都判 wrong → 42 次错，必须是第一
    top = d["top_wrong_words"]
    assert top, "错题榜单不应为空"
    assert top[0]["word_id"] == SEED.words_a[3]
    assert top[0]["error_count"] == 42
    assert top[0]["student_count"] == 42
    counts = [w["error_count"] for w in top]
    assert counts == sorted(counts, reverse=True), f"应按错误次数降序：{counts}"
    # empty 不是错（口径 §1.1），所以不该出现任何 empty 造成的错题
    assert all(w["error_count"] > 0 for w in top)

    # 完成率：42 人各练过前 4 个词（第 5 个词全是空结果，按口径不算练过）→ 4 / 10
    assert d["practiced_word_count"] == 4
    assert d["scope_word_count"] == 10
    assert d["completion_ratio"] == pytest.approx(0.4)

    assert d["avg_answer_ms"] is not None and d["avg_answer_ms"] > 0
    assert d["avg_session_ms"] is not None and d["avg_session_ms"] > 0
    assert d["today_date"] == beijing_day(0)
    assert d["today_active_students"] >= 1


def test_u1_class_overview_range_filter(client):
    """日期范围能收窄样本，且趋势点数随范围变化。"""
    headers = _admin_headers(client)
    body = client.get(
        f"/api/stats/class/{SEED.class_a}",
        params={"date_from": beijing_day(-1), "date_to": beijing_day(0)},
        headers=headers,
    ).json()["data"]
    assert len(body["trend"]) == 2
    assert 0 < body["answer_count"] < SEED.expected_counted + 42

    # 只查一个肯定没有数据的历史区间 → 全 0，但**趋势仍要有点**（补零，不是空数组）
    body = client.get(
        f"/api/stats/class/{SEED.class_a}",
        params={"date_from": "2000-01-01", "date_to": "2000-01-03"},
        headers=headers,
    ).json()["data"]
    assert body["answer_count"] == 0
    assert body["accuracy"] == 0.0
    assert len(body["trend"]) == 3
    assert all(p["item_count"] == 0 and p["accuracy"] == 0.0 for p in body["trend"])


def test_class_not_found(client):
    body = client.get("/api/stats/class/999999", headers=_admin_headers(client)).json()
    assert body["code"] == 20004, body


# =========================================================================== U2


def test_u2_unit_overview(client):
    """U2：难词按正确率升序；未完成学生 = 练过但没练满的学生。"""
    body = client.get(
        f"/api/stats/unit/{SEED.unit_a}", headers=_admin_headers(client)
    ).json()
    assert body["code"] == 0, body
    d = body["data"]

    assert d["unit_name"] == "Unit 1 Hello"
    assert d["word_total"] == 10
    assert d["student_count"] == 42
    assert d["accuracy"] == pytest.approx(SEED.expected_accuracy)
    assert d["counted_count"] == SEED.expected_counted

    # 练过 4 个词（word00~word03；word04 全空结果，不算练过），
    # 掌握的是每个偶数学生的 word00/word01 → 去重后 2 个词
    assert d["practiced_word_count"] == 4
    assert d["mastered_word_count"] == 2
    assert d["mastered_ratio"] == pytest.approx(0.2)

    hard = d["hard_words"]
    assert hard, "难词榜不应为空"
    accs = [w["accuracy"] for w in hard]
    assert accs == sorted(accs), f"应按正确率升序：{accs}"
    # word03 全错 → 正确率 0，必须排第一
    assert hard[0]["word_id"] == SEED.words_a[3]
    assert hard[0]["accuracy"] == 0.0
    assert hard[0]["error_count"] == 42
    assert hard[0]["counted_count"] == 42, "empty 不该进分母"
    # word04 只有 empty → 没有任何有效样本，不该出现在难词榜里
    assert SEED.words_a[4] not in [w["word_id"] for w in hard]

    # 未完成名单：42 人都只练了 4/10 个词
    unfinished = d["unfinished_students"]
    assert len(unfinished) == 42
    assert all(s["practiced_words"] == 4 for s in unfinished), (
        "视图的 word_practiced 必须与单元口径的 practiced_word_count 一致"
    )
    assert all(s["missing_words"] == 6 for s in unfinished)
    assert all(s["username"].startswith("a") for s in unfinished)

    # 一个已练满的学生不该出现在名单里：临时把某人的记录拉满再查
    with write_tx() as conn:
        student = SEED.students_a[0]
        for w in SEED.words_a[4:]:
            conn.execute(
                """
                INSERT INTO word_mastery (student_id, word_id, total_count, correct_count,
                                          wrong_count, last_result, last_answer_at)
                VALUES (?,?,1,1,0,1,'2026-01-01T00:00:00Z')
                """,
                (student, w),
            )
    body2 = client.get(
        f"/api/stats/unit/{SEED.unit_a}", headers=_admin_headers(client)
    ).json()["data"]
    ids = [s["student_id"] for s in body2["unfinished_students"]]
    assert student not in ids, "练满的学生不该留在未完成名单"
    assert len(ids) == 41


def test_unit_not_found(client):
    body = client.get("/api/stats/unit/999999", headers=_admin_headers(client)).json()
    assert body["code"] == 30001, body


# =========================================================================== U3


def test_u3_student_report(client):
    """U3：个人报告的 `accuracy` 与单一口径函数一致；`recitations` 有评级。"""
    sid = SEED.students_a[0]
    body = client.get(
        f"/api/stats/student/{sid}", headers=_admin_headers(client)
    ).json()
    assert body["code"] == 0, body
    d = body["data"]

    assert d["username"] == "a000"
    assert d["class_id"] == SEED.class_a
    assert d["class_name"] == "五(1)班"
    assert d["session_count"] == 2, "1 场英语 + 1 场语文背诵"
    assert d["finished_session_count"] == 2
    assert d["practice_days"] >= 1
    assert d["recitation_count"] == 1

    # 与 `answers` 手工核对：a000 是偶数 → exact,exact,loose,wrong,empty
    expected = StatsService.calc_accuracy(3, 4)
    assert d["accuracy"] == pytest.approx(expected)
    assert d["correct_count"] == 3
    assert d["counted_count"] == 4
    assert d["answer_count"] == 5
    # 与 BE-02 的同一口径函数交叉验证（U5 的一部分）
    assert d["accuracy"] == pytest.approx(UserService.student_stats(sid)["accuracy"])

    assert d["mastered_words"] == 2
    assert d["streak_days"] >= 1

    recs = d["recitations"]
    assert recs and recs[0]["grade"] == "优秀"
    assert recs[0]["text_title"] == "静夜思"
    assert recs[0]["covered_ratio"] == 1.0
    assert recs[0]["first_voice_ms"] == 800

    assert d["recent_sessions"]
    zh = [s for s in d["recent_sessions"] if s["content_type"] == "chinese"]
    assert zh and zh[0]["unit_name"] == "第一单元 古诗"


def test_student_report_not_found(client):
    body = client.get("/api/stats/student/999999", headers=_admin_headers(client)).json()
    assert body["code"] != 0
    # 管理者不是学生，也不能拿它当学生查
    with read_conn() as conn:
        admin_id = int(conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()["id"])
    body = client.get(f"/api/stats/student/{admin_id}", headers=_admin_headers(client)).json()
    assert body["code"] != 0, "admin 账号不应被当成学生返回报告"


# =========================================================================== U4


def test_u4_accuracy_excludes_empty(client):
    """U4：2 exact + 1 wrong + 1 empty → 正确率 = 2/3（**排除 empty**）。

    这是全项目口径的守门用例：把 empty 算进分母会得到 2/4 = 0.5。
    """
    body = client.get(
        f"/api/stats/student/{SEED.student_b}", headers=_admin_headers(client)
    ).json()["data"]

    assert body["answer_count"] == 4
    assert body["counted_count"] == 3, "empty 必须从分母里剔除"
    assert body["correct_count"] == 2
    assert body["accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert body["accuracy"] != pytest.approx(0.5), "把 empty 计入分母就会变成 0.5"

    # 口径函数本身
    assert StatsService.calc_accuracy(2, 3) == pytest.approx(0.6667)
    assert StatsService.calc_accuracy(0, 0) == 0.0, "分母 0 要返回 0.0 而不是崩"
    assert StatsService.calc_accuracy(5, 0) == 0.0, "分母 0 时分子再大也是 0.0"


# =========================================================================== U5


def test_u5_accuracy_consistent_across_endpoints(client):
    """U5：同一份数据用不同接口查正确率 → **全部一致**（口径函数唯一）。

    六(1)班只有 1 名学生、1 个单元，且该生的全部答题都落在这个单元的词上，
    所以「班级 / 单元 / 学生」三个口径的样本集合**完全相同**，必须得到同一个数。
    若项目里存在第二份公式实现，这个用例是最可能抓住它的地方。
    """
    headers = _admin_headers(client)
    cls = client.get(f"/api/stats/class/{SEED.class_b}", headers=headers).json()["data"]
    unit = client.get(f"/api/stats/unit/{SEED.unit_b}", headers=headers).json()["data"]
    stu = client.get(f"/api/stats/student/{SEED.student_b}", headers=headers).json()["data"]
    direct = StatsService.calc_accuracy(2, 3)
    be02 = UserService.student_stats(SEED.student_b)["accuracy"]

    values = {
        "class": cls["accuracy"],
        "unit": unit["accuracy"],
        "student": stu["accuracy"],
        "calc_accuracy": direct,
        "UserService": be02,
        "handwritten": round(2 / 3, 4),
    }
    assert len(set(values.values())) == 1, f"口径出现分歧：{values}"

    # 分母也必须一致（只对精度还不够，得连样本数一起对上）
    assert cls["counted_count"] == unit["counted_count"] == stu["counted_count"] == 3

    # 而且这份实现确实**只有一处**：统计层是转发，不是第二份公式
    assert StatsService.calc_accuracy(7, 9) == UserService._accuracy(7, 9)


# =========================================================================== U11


def test_u11_beijing_day_boundary(client):
    """U11：服务器 UTC 23:30（北京次日 07:30）时，「今天」要按北京时间归属。

    六(1)班那 4 条答题的北京时间是**今天 07:30**，对应 UTC 是**昨天 23:30**。
    所以：按北京时间查「今天」必须查得到；按 UTC 切天则落在昨天。
    """
    today = beijing_day(0)
    with read_conn() as conn:
        utc_days = [
            str(r["d"])
            for r in conn.execute(
                "SELECT DISTINCT substr(created_at, 1, 10) AS d FROM answers WHERE student_id = ?",
                (SEED.student_b,),
            ).fetchall()
        ]
    assert utc_days and utc_days[0] != today, (
        f"样本没有跨日，用例失去意义（UTC 天 = {utc_days}，北京今天 = {today}）"
    )

    headers = _admin_headers(client)
    body = client.get(
        f"/api/stats/class/{SEED.class_b}",
        params={"date_from": today, "date_to": today},
        headers=headers,
    ).json()["data"]

    assert body["answer_count"] == 4, (
        f"按北京时间切天应能查到今天的 4 条记录，实际 {body['answer_count']}"
    )
    assert body["today_active_students"] == 1
    assert body["today_session_count"] == 1

    # 反向确认：按 UTC 昨天去查，就查不到（证明切天逻辑真的按 +8 走）
    yesterday_utc_side = beijing_day(-1)
    body2 = client.get(
        f"/api/stats/class/{SEED.class_b}",
        params={"date_from": yesterday_utc_side, "date_to": yesterday_utc_side},
        headers=headers,
    ).json()["data"]
    assert body2["answer_count"] == 0, (
        "北京时间今天的记录不该出现在昨天；这说明切天没按 +8 走"
    )


# =========================================================================== U6


def test_u6_appeals_pending_filter(client):
    """U6：`status=pending` 只返回 `appealed=1` 且 `appeal_result IS NULL` 的记录。"""
    headers = _admin_headers(client)
    body = client.get("/api/appeals", params={"status": "pending"}, headers=headers).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["total"] >= 1

    ids = [i["appeal_id"] for i in data["items"]]
    with read_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, appealed, appeal_result, is_correct, match_type
            FROM answers WHERE appealed = 1 AND appeal_result IS NULL
            """
        ).fetchall()
    assert set(ids) == {int(r["id"]) for r in rows}
    assert all(r["appealed"] == 1 and r["appeal_result"] is None for r in rows)

    # 默认（不带 status）也是 pending
    default = client.get("/api/appeals", headers=headers).json()["data"]
    assert default["total"] == data["total"], "首屏默认必须是待复核"

    # 尚未复核的记录不应出现在 upheld/rejected 里
    for status in ("upheld", "rejected"):
        other = client.get(
            "/api/appeals", params={"status": status}, headers=headers
        ).json()["data"]
        assert not (set(ids) & {i["appeal_id"] for i in other["items"]})

    # 非法 status 要报错，不能静默当 pending
    bad = client.get("/api/appeals", params={"status": "whatever"}, headers=headers).json()
    assert bad["code"] == 10001, bad


# =========================================================================== U7


def test_u7_review_upheld_keeps_is_correct(client):
    """U7：复核为 `upheld` → `appeal_result='upheld'`；`is_correct` **未变**；有审计。"""
    headers = _admin_headers(client)
    pending = client.get("/api/appeals", headers=headers).json()["data"]["items"]
    assert pending, "没有待复核记录，后续断言失去意义"
    target = pending[0]["appeal_id"]

    with read_conn() as conn:
        before = dict(
            conn.execute(
                "SELECT is_correct, match_type, expected, asr_text FROM answers WHERE id = ?",
                (target,),
            ).fetchone()
        )

    resp = client.post(
        f"/api/appeals/{target}/review",
        json={"result": "upheld", "remark": "录音回放确认学生读对了"},
        headers=headers,
    ).json()
    assert resp["code"] == 0, resp
    assert resp["data"]["changed"] is True
    assert resp["data"]["result"] == "upheld"
    assert resp["data"]["is_correct"] == before["is_correct"]

    with read_conn() as conn:
        after = dict(
            conn.execute(
                "SELECT is_correct, match_type, expected, asr_text, appeal_result "
                "FROM answers WHERE id = ?",
                (target,),
            ).fetchone()
        )
    assert after["appeal_result"] == "upheld"
    # ⛔ 硬约束：原判定一个字都不许动
    assert after["is_correct"] == before["is_correct"], "复核绝不能改 is_correct"
    assert after["match_type"] == before["match_type"]
    assert after["expected"] == before["expected"]
    assert after["asr_text"] == before["asr_text"]

    # 审计
    records = _audits("appeal.review")
    assert records, "复核必须写 audit_logs"
    latest = records[0]
    assert latest["result"] == "upheld"
    assert latest["remark"] == "录音回放确认学生读对了"
    assert latest["previous_result"] is None
    assert latest["is_correct_unchanged"] == before["is_correct"]
    assert latest["expected"] == before["expected"]

    # 复核后从 pending 消失、出现在 upheld
    still_pending = {
        i["appeal_id"]
        for i in client.get("/api/appeals", headers=headers).json()["data"]["items"]
    }
    assert target not in still_pending
    upheld = {
        i["appeal_id"]
        for i in client.get(
            "/api/appeals", params={"status": "upheld"}, headers=headers
        ).json()["data"]["items"]
    }
    assert target in upheld

    # 列表要能把复核痕迹带回来（remark 存在审计里，前端不必再查一遍）
    item = next(
        i
        for i in client.get(
            "/api/appeals", params={"status": "upheld"}, headers=headers
        ).json()["data"]["items"]
        if i["appeal_id"] == target
    )
    assert item["review_remark"] == "录音回放确认学生读对了"
    assert item["reviewer_name"], "复核人姓名要能显示"
    assert item["reviewed_at"]

    # 幂等重复提交：不报错、不再写审计、结论不变
    audit_count = len(_audits("appeal.review"))
    again = client.post(
        f"/api/appeals/{target}/review",
        json={"result": "upheld", "remark": "重复点了一次"},
        headers=headers,
    ).json()
    assert again["code"] == 0
    assert again["data"]["changed"] is False
    assert len(_audits("appeal.review")) == audit_count, "幂等提交不该再写审计"


def test_review_switch_result_and_validation(client):
    """改判（upheld → rejected）要留痕；非法入参与无申诉记录要明确报错。"""
    headers = _admin_headers(client)
    upheld_items = client.get(
        "/api/appeals", params={"status": "upheld"}, headers=headers
    ).json()["data"]["items"]
    target = upheld_items[0]["appeal_id"]

    resp = client.post(
        f"/api/appeals/{target}/review",
        json={"result": "rejected", "remark": "再次核对后维持原判"},
        headers=headers,
    ).json()
    assert resp["code"] == 0, resp
    assert resp["data"]["previous_result"] == "upheld", "改判要记录改之前是什么"

    records = _audits("appeal.review")
    assert records[0]["previous_result"] == "upheld"
    assert records[0]["result"] == "rejected"

    # 非法 result
    bad = client.post(
        f"/api/appeals/{target}/review", json={"result": "maybe"}, headers=headers
    ).json()
    assert bad["code"] == 10001, bad

    # remark 过长 → 10001
    long_remark = client.post(
        f"/api/appeals/{target}/review",
        json={"result": "rejected", "remark": "字" * 300},
        headers=headers,
    ).json()
    assert long_remark["code"] == 10001, long_remark

    # 不存在的申诉
    missing = client.post(
        "/api/appeals/999999/review", json={"result": "upheld"}, headers=headers
    ).json()
    assert missing["code"] == 10004, missing

    # 没有申诉标记的记录不能复核
    with read_conn() as conn:
        plain = int(
            conn.execute(
                "SELECT id FROM answers WHERE appealed = 0 LIMIT 1"
            ).fetchone()["id"]
        )
    plain_resp = client.post(
        f"/api/appeals/{plain}/review", json={"result": "upheld"}, headers=headers
    ).json()
    assert plain_resp["code"] == 10004, plain_resp


# =========================================================================== U8


def test_u8_audio_available_always_false(client):
    """U8：申诉列表的 `audio_available` 恒为 `false`（一期不存录音）。"""
    headers = _admin_headers(client)
    for status in ("pending", "upheld", "rejected", "all"):
        body = client.get("/api/appeals", params={"status": status}, headers=headers).json()
        assert body["code"] == 0, body
        for item in body["data"]["items"]:
            assert item["audio_available"] is False, f"{status} 里出现了 True"
            assert "audio_url" not in item


# =========================================================================== U9


def test_u9_rollup_moves_old_detail(client):
    """U9：90 天前的明细被聚合进 `asr_daily_stats` 并删除；90 天内的保留。"""
    old_day = beijing_day(-120)
    old_day2 = beijing_day(-100)
    recent_day = beijing_day(0)

    with write_tx() as conn:
        # 清理上次跑留下的
        conn.execute("DELETE FROM asr_logs")
        conn.execute("DELETE FROM asr_daily_stats")
        for i in range(5):
            conn.execute(
                """
                INSERT INTO asr_logs (provider, model, lang, task_type, audio_ms,
                                      latency_ms, status, created_at)
                VALUES ('aliyun', 'fun-asr-flash', 'en', 'word', 1500, 300, 'ok', ?)
                """,
                (_utc(old_day, 9, i),),
            )
        conn.execute(
            """
            INSERT INTO asr_logs (provider, model, lang, task_type, audio_ms,
                                  latency_ms, status, created_at)
            VALUES ('aliyun', 'fun-asr-flash', 'en', 'word', 2000, 400, 'error', ?)
            """,
            (_utc(old_day, 10, 0),),
        )
        for i in range(3):
            conn.execute(
                """
                INSERT INTO asr_logs (provider, model, lang, task_type, audio_ms,
                                      latency_ms, status, created_at)
                VALUES ('mock', 'mock-asr', 'zh', 'spell', 1000, 120, 'empty', ?)
                """,
                (_utc(old_day2, 9, i),),
            )
        for i in range(4):
            conn.execute(
                """
                INSERT INTO asr_logs (provider, model, lang, task_type, audio_ms,
                                      latency_ms, status, created_at)
                VALUES ('aliyun', 'fun-asr-flash', 'en', 'word', 1100, 250, 'ok', ?)
                """,
                (_utc(recent_day, 11, i),),
            )
        # 91 天前：刚好在保留线之外，必须被聚合（边界）
        conn.execute(
            """
            INSERT INTO asr_logs (provider, model, lang, task_type, audio_ms,
                                  latency_ms, status, created_at)
            VALUES ('aliyun', 'fun-asr-flash', 'en', 'word', 900, 200, 'ok', ?)
            """,
            (_utc(beijing_day(-91), 9, 0),),
        )

    # dry_run 先看规模，且**不能**改动数据
    preview = asr_daily_rollup.run_rollup(dry_run=True)
    assert preview["dry_run"] is True
    assert preview["deleted_rows"] == 0
    assert preview["rolled_rows"] == 10, f"5+1+3+1=10，实际 {preview['rolled_rows']}"
    with read_conn() as conn:
        assert int(conn.execute("SELECT COUNT(*) AS n FROM asr_logs").fetchone()["n"]) == 14

    result = asr_daily_rollup.run_rollup()
    assert result["keep_days"] == 90
    assert result["cutoff"] == beijing_day(-90)
    assert result["deleted_rows"] == 10
    assert result["rolled_rows"] == 10
    assert result["rolled_days"] == 3, f"3 个 (天, 厂商) 分组，实际 {result['rolled_days']}"

    with read_conn() as conn:
        remaining = [
            str(r["d"])
            for r in conn.execute(
                "SELECT DISTINCT date(datetime(created_at, '+8 hours')) AS d FROM asr_logs"
            ).fetchall()
        ]
        stats = {
            (str(r["stat_date"]), r["provider"]): dict(r)
            for r in conn.execute("SELECT * FROM asr_daily_stats").fetchall()
        }

    assert remaining == [recent_day], f"只应保留今天，实际 {remaining}"
    assert (old_day, "aliyun") in stats
    row = stats[(old_day, "aliyun")]
    assert row["call_count"] == 6, "5 ok + 1 error"
    assert row["ok_count"] == 5
    assert row["error_count"] == 1
    assert row["empty_count"] == 0
    assert row["total_audio_ms"] == 5 * 1500 + 2000
    assert row["avg_latency_ms"] == 300, "只对 ok 行求平均"

    row2 = stats[(old_day2, "mock")]
    assert row2["call_count"] == 3 and row2["empty_count"] == 3 and row2["ok_count"] == 0
    assert row2["avg_latency_ms"] is None, "没有 ok 行 → 平均延迟为 None，不能是 0"

    row3 = stats[(beijing_day(-91), "aliyun")]
    assert row3["call_count"] == 1, "91 天前（超出保留线）必须被聚合"

    # 审计留痕（聚合是删数据动作，必须可追）
    records = _audits("asr.rollup")
    assert records, "聚合删了明细就要写审计"
    assert records[0]["deleted_rows"] == 10
    assert records[0]["keep_days"] == 90

    # 再跑一次是空跑（幂等），不会重复累加到 asr_daily_stats
    again = asr_daily_rollup.run_rollup()
    assert again["deleted_rows"] == 0
    assert again["rolled_days"] == 0
    with read_conn() as conn:
        assert int(
            conn.execute(
                "SELECT call_count FROM asr_daily_stats WHERE stat_date = ? AND provider = ?",
                (old_day, "aliyun"),
            ).fetchone()["call_count"]
        ) == 6, "重复执行不能把汇总变成 12"


def test_rollup_retention_snapshot(client):
    """`detail_retention()` 给排障看的保留现状。"""
    snap = asr_daily_rollup.detail_retention()
    assert snap["keep_days"] == 90
    assert snap["rolled_days"] >= 3
    assert snap["rolled_earliest"] <= snap["rolled_latest"]


def test_rollup_handles_scheduler_lazy_import(client):
    """F9：调度器的日任务要能真的调到聚合（延迟导入路径不能是死的）。

    调度器里是 `from app.tasks import asr_daily_rollup` + `run_rollup()`。
    模块名或函数名改动会让它静默跳过（`ImportError` 被吞成 debug 日志），
    这个用例就是防止这种「改了名没人发现」。
    """
    import asyncio

    from app.tasks import scheduler

    result = asyncio.run(scheduler.run_daily_job_now("asr_rollup"))
    assert isinstance(result, dict)
    assert "deleted_rows" in result and "cutoff" in result


# =========================================================================== U10


def test_u10_asr_stats(client):
    """U10：`GET /api/stats/asr` 返回按天与厂商的用量、平均延迟、失败率。

    ⚠️ 默认范围只有近 7 天，而被聚合掉的老数据在 120 天前 —— 所以看老数据
    必须显式给一个够宽的范围（上限 180 天）。这不是接口的问题，是「默认近 7 天」
    这个设定的必然结果，用例里要显式体现。
    """
    headers = _admin_headers(client)
    body = client.get("/api/stats/asr", headers=headers).json()
    assert body["code"] == 0, body
    d = body["data"]

    assert d["detail_keep_days"] == 90
    assert d["daily"], "应有数据（U9 刚造过）"

    today = beijing_day(0)
    today_rows = [r for r in d["daily"] if r["stat_date"] == today and r["provider"] == "aliyun"]
    assert today_rows, f"今天的明细应出现在结果里：{d['daily']}"
    row = today_rows[0]
    assert row["source"] == "detail"
    assert row["call_count"] == 4
    assert row["ok_count"] == 4
    assert row["avg_latency_ms"] == 250
    assert row["failure_rate"] == 0.0
    assert row["total_audio_ms"] == 4 * 1100

    # 默认窗口（近 7 天）里**不该**出现 120 天前的老数据
    assert all(r["stat_date"] > beijing_day(-7) for r in d["daily"]), (
        "默认近 7 天的窗口漏进了老数据"
    )

    # 放宽范围后，老数据从 `asr_daily_stats` 里取（source 不同，用来对账）
    wide = client.get(
        "/api/stats/asr",
        params={"date_from": beijing_day(-130), "date_to": today},
        headers=headers,
    ).json()["data"]
    old = [r for r in wide["daily"] if r["stat_date"] == beijing_day(-120)]
    assert old and old[0]["source"] == "daily_stats", f"老数据应来自 asr_daily_stats：{wide['daily']}"
    assert old[0]["call_count"] == 6
    assert old[0]["total_audio_ms"] == 5 * 1500 + 2000
    # 1 error / 6 → 0.1667
    assert old[0]["failure_rate"] == pytest.approx(round(1 / 6, 4))
    # empty 不算失败：3 条 empty 的失败率必须是 0
    empty_row = [r for r in wide["daily"] if r["provider"] == "mock"][0]
    assert empty_row["empty_count"] == 3
    assert empty_row["failure_rate"] == 0.0, "empty 不能算失败（口径 §1.4）"

    by_provider = {p["provider"]: p for p in wide["by_provider"]}
    assert set(by_provider) == {"aliyun", "mock"}
    assert by_provider["aliyun"]["call_count"] == 4 + 6 + 1
    assert by_provider["aliyun"]["failed_count"] == 1
    assert by_provider["aliyun"]["avg_latency_ms"] is not None

    totals = wide["totals"]
    assert totals["call_count"] == 4 + 6 + 3 + 1
    assert totals["total_audio_minutes"] > 0
    assert 0 <= totals["failure_rate"] <= 1

    # provider 过滤
    only_mock = client.get(
        "/api/stats/asr",
        params={"provider": "mock", "date_from": beijing_day(-130), "date_to": today},
        headers=headers,
    ).json()["data"]
    assert {r["provider"] for r in only_mock["daily"]} == {"mock"}
    assert {p["provider"] for p in only_mock["by_provider"]} == {"mock"}

    # 日期范围过滤
    narrow = client.get(
        "/api/stats/asr",
        params={"date_from": today, "date_to": today},
        headers=headers,
    ).json()["data"]
    assert {r["stat_date"] for r in narrow["daily"]} == {today}


# =========================================================================== U12


def test_u12_audit_logs(client):
    """U12：`GET /api/audit-logs` 分页与筛选正确。"""
    headers = _admin_headers(client)

    body = client.get("/api/audit-logs", params={"page": 1, "page_size": 5}, headers=headers).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert set(data) == {"items", "total", "page", "page_size"}
    assert data["page_size"] == 5
    assert len(data["items"]) <= 5
    assert data["total"] >= 2, "至少有 1 条复核 + 1 条聚合审计"

    # 分页真的翻得动：第 2 页与第 1 页不重合
    page2 = client.get(
        "/api/audit-logs", params={"page": 2, "page_size": 5}, headers=headers
    ).json()["data"]
    assert page2["page"] == 2
    overlap = {i["id"] for i in data["items"]} & {i["id"] for i in page2["items"]}
    assert not overlap, "第二页不该和第一页重合"

    # 按动作筛选（点分结构 → 支持按域前缀筛）
    by_action = client.get(
        "/api/audit-logs", params={"action": "appeal.review"}, headers=headers
    ).json()["data"]
    assert by_action["total"] >= 1
    assert all(i["action"] == "appeal.review" for i in by_action["items"])
    # 前缀匹配：`appeal.` 应能覆盖 `appeal.review`
    prefix = client.get(
        "/api/audit-logs", params={"action": "appeal."}, headers=headers
    ).json()["data"]
    assert prefix["total"] == by_action["total"]

    # LIKE 通配符要被转义（否则 `%` 会把全表捞出来）
    wildcard = client.get(
        "/api/audit-logs", params={"action": "%"}, headers=headers
    ).json()["data"]
    assert wildcard["total"] == 0, "`%` 必须被当成普通字符"

    # 按用户筛
    with read_conn() as conn:
        admin_id = int(conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()["id"])
    by_user = client.get(
        "/api/audit-logs", params={"user_id": admin_id}, headers=headers
    ).json()["data"]
    assert by_user["total"] >= 1
    assert all(i["user_id"] == admin_id for i in by_user["items"])
    assert by_user["items"][0]["username"] == "admin"

    # 时间筛选 + 非法值
    today = beijing_day(0)
    by_day = client.get(
        "/api/audit-logs",
        params={"date_from": today, "date_to": today},
        headers=headers,
    ).json()["data"]
    assert by_day["total"] >= 1

    bad = client.get(
        "/api/audit-logs", params={"date_from": "2026/01/01"}, headers=headers
    ).json()
    assert bad["code"] == 10001 and "date_from" in bad["message"], bad

    bad_page = client.get(
        "/api/audit-logs", params={"page_size": 999}, headers=headers
    ).json()
    assert bad_page["code"] == 10001, bad_page


# =========================================================================== U13


def test_u13_export_class_xlsx(client):
    """U13：导出班级成绩 xlsx —— 能正常打开，含学生/正确率/场次。"""
    resp = client.get(
        "/api/stats/export",
        params={"type": "class", "id": SEED.class_a},
        headers=_admin_headers(client),
    )
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]
    assert "%E6%88%90%E7%BB%A9" in resp.headers["content-disposition"], (
        "中文文件名要做 RFC 5987 编码，否则浏览器下载会乱码"
    )

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(resp.content))
    ws = wb.active
    header = [c.value for c in ws[1]]
    assert header == list(StatsService.CLASS_EXPORT_HEADERS)
    assert ws.max_row == 42 + 1, f"42 名学生 + 表头，实际 {ws.max_row} 行"

    idx = {name: i for i, name in enumerate(header)}
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    # a000：正确率 3/4 = 75.0%
    a000 = next(r for r in rows if r[idx["学号"]] == "a000")
    assert a000[idx["练习场次"]] == 2
    assert a000[idx["正确率"]] == "75.0%"
    assert a000[idx["已掌握词数"]] == 2
    # 正确率的字符串形式与口径函数一致
    assert a000[idx["正确率"]] == f"{StatsService.calc_accuracy(3, 4):.1%}"


def test_u13_export_student_xlsx(client):
    """U13：导出学生成绩 —— 一行一场，正确率同口径。"""
    resp = client.get(
        "/api/stats/export",
        params={"type": "student", "id": SEED.student_b},
        headers=_admin_headers(client),
    )
    assert resp.status_code == 200

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(resp.content))
    ws = wb.active
    header = [c.value for c in ws[1]]
    assert header == list(StatsService.STUDENT_EXPORT_HEADERS)
    assert ws.max_row == 1 + 1, "乙班学生只有 1 场"
    row = [c.value for c in ws[2]]
    idx = {name: i for i, name in enumerate(header)}
    assert row[idx["正确率"]] == "66.7%"
    assert row[idx["单元"]] == "Unit 1 六"
    assert row[idx["类型"]] == "英语"


def test_u13_export_validation(client):
    headers = _admin_headers(client)
    assert client.get("/api/stats/export", params={"type": "x", "id": 1},
                      headers=headers).json()["code"] == 10001
    assert client.get("/api/stats/export", params={"type": "class"},
                      headers=headers).json()["code"] == 10001
    assert client.get("/api/stats/export", params={"type": "class", "id": 0},
                      headers=headers).json()["code"] == 10001
    assert client.get("/api/stats/export", params={"type": "class", "id": 999999},
                      headers=headers).json()["code"] == 20004


# =========================================================================== 鉴权与参数


@pytest.mark.parametrize(
    "path,params",
    [
        ("/api/stats/class/1", {}),
        ("/api/stats/unit/1", {}),
        ("/api/stats/student/1", {}),
        ("/api/stats/asr", {}),
        ("/api/audit-logs", {}),
        ("/api/stats/export", {"type": "class", "id": 1}),
        ("/api/appeals", {}),
    ],
)
def test_all_stats_endpoints_require_admin(client, path, params):
    """统计与申诉都是**管理者专有**：无 token → 10002，学生 token → 10003。"""
    assert client.get(path, params=params).json()["code"] == 10002

    registered = client.post(
        "/api/auth/register",
        json={
            "username": "be07student",
            "password": "student-pass-7711",
            "real_name": "统计测试学生",
        },
    ).json()
    if registered.get("code") == 0:
        token = registered["data"]["access_token"]
    else:
        token = client.post(
            "/api/auth/login",
            json={"username": "be07student", "password": "student-pass-7711", "role": "student"},
        ).json()["data"]["access_token"]

    body = client.get(path, params=params, headers={"Authorization": f"Bearer {token}"}).json()
    assert body["code"] == 10003, f"{path} 学生不该能访问：{body}"


def test_date_params_validated(client):
    """日期参数的错误必须带字段名（管理端要能标到具体输入框）。"""
    headers = _admin_headers(client)
    base = f"/api/stats/class/{SEED.class_a}"

    bad_from = client.get(
        base, params={"date_from": "20260924"}, headers=headers
    ).json()
    assert bad_from["code"] == 10001
    assert "date_from" in bad_from["message"], bad_from

    reversed_range = client.get(
        base,
        params={"date_from": beijing_day(0), "date_to": beijing_day(-3)},
        headers=headers,
    ).json()
    assert reversed_range["code"] == 10001
    assert "date_from" in reversed_range["message"]

    too_long = client.get(
        base, params={"date_from": "2020-01-01", "date_to": beijing_day(0)}, headers=headers
    ).json()
    assert too_long["code"] == 10001
    assert "180" in too_long["message"]

    bad_top = client.get(base, params={"top_n": "abc"}, headers=headers).json()
    assert bad_top["code"] == 10001
    out_of_range = client.get(base, params={"top_n": 999}, headers=headers).json()
    assert out_of_range["code"] == 10001, "越界要报错，不能静默夹成 50"


def test_top_wrong_words_respects_top_n(client):
    headers = _admin_headers(client)
    body = client.get(
        f"/api/stats/class/{SEED.class_a}", params={"top_n": 2}, headers=headers
    ).json()["data"]
    assert len(body["top_wrong_words"]) == 2


def test_no_runtime_state_pollutes_settings(client):
    """统计模块是只读的：跑完全部查询后 `settings` 表不能多出行。"""
    with read_conn() as conn:
        n = int(conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()["n"])
    assert n == 20, f"settings 种子应为 20 条，实际 {n}"


def test_stats_service_does_not_write_business_tables(client):
    """统计接口**只读**：调用一轮后业务表行数不变（防止顺手 UPDATE 了什么东西）。"""
    tables = ("answers", "sessions", "recitations", "word_mastery")
    headers = _admin_headers(client)

    def snapshot() -> dict[str, int]:
        with read_conn() as conn:
            return {
                t: int(conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"])
                for t in tables
            }

    before = snapshot()
    client.get(f"/api/stats/class/{SEED.class_a}", headers=headers)
    client.get(f"/api/stats/unit/{SEED.unit_a}", headers=headers)
    client.get(f"/api/stats/student/{SEED.students_a[0]}", headers=headers)
    client.get("/api/stats/asr", headers=headers)
    client.get("/api/appeals", params={"status": "all"}, headers=headers)
    assert snapshot() == before


def test_sqlite_datetime_offset_binding():
    """守住一个容易写错的前提：`datetime(x, ?)` 的第二参**可以**是绑定值。

    全项目的「按北京时间切天」都写成 `date(datetime(created_at, ?))` + 绑定 `'+8 hours'`。
    万一将来 SQLite/绑定行为变了，这个用例会第一时间红，而不是等到统计数字悄悄错位。
    """
    conn = sqlite3.connect(":memory:")
    try:
        got = conn.execute(
            "SELECT date(datetime(?, ?))", ("2026-09-24T23:30:00Z", "+8 hours")
        ).fetchone()[0]
    finally:
        conn.close()
    assert got == "2026-09-25"
    # 与 Python 侧算出来的一致
    assert got == (
        datetime(2026, 9, 24, 23, 30, tzinfo=timezone.utc) + timedelta(hours=8)
    ).date().isoformat()
