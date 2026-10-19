"""旧契约 /api/me/* 兼容层 —— 让已分发的学生端 App（10-11 版 APK）不重打包继续可用。

背景：simplify（003）删除了「多用户背诵系统」的 /api/me/* 接口，但 App 首页
仍按旧契约 §8.3/§8.4/§8.8/§8.10 取数，拿不到就显示本地缓存旧数据，导致
「后台与 App 数据不一致」。本模块用新表拼出旧 JSON 形状：

- GET  /api/me/units          → UnitsPage{items:[UnitItem]}（契约 §8.3）
- GET  /api/me/progress       → ProgressOverview（今日统计来自 practice_logs，§8.4）
- GET  /api/me/wrong-words    → {total, items:[]}（App 只读 total，§8.8）
- GET  /api/me/wrong-chars    → {total:0, items:[]}（语文不存在，§8.10）
- PUT  /api/me/profile        → ok({})（年级学期偏好在简化系统无载体，空操作）
- GET  /api/units/{id}/words  → {words:[WordItem]}（items.zh→cn，mastery 取错题本）
- GET  /api/units/{id}/texts  → {texts:[]}（简化系统无课文）

已知口径差异（旧字段在新系统无数据源，按保守值下发）：
- practiced / mastered 恒 0（无逐词条掌握度表），accuracy 按 practice_logs 汇总；
- status 仅 not_started / practiced_with_error / practiced_ok 三态。

todo11 App 首页迁移到新契约并重新打包后，整体删除本模块。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.db import read_conn
from app.core.response import ok

router = APIRouter(tags=["me-compat"])


@router.get("/api/me/units")
async def me_units() -> dict:
    """单元卡列表：全部单元（一级+二级平铺），词条数与错题数来自新表。"""
    with read_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.name,
                   (SELECT COUNT(*) FROM items i WHERE i.unit_id = u.id) AS total,
                   (SELECT COUNT(*) FROM wrong_book w JOIN items i2 ON i2.id = w.item_id
                     WHERE i2.unit_id = u.id AND w.resolved_at IS NULL) AS wrong_cnt,
                   (SELECT SUM(pl.total) FROM practice_logs pl, json_each(pl.unit_ids) je
                     WHERE CAST(je.value AS INTEGER) = u.id) AS p_total,
                   (SELECT SUM(pl.correct) FROM practice_logs pl, json_each(pl.unit_ids) je
                     WHERE CAST(je.value AS INTEGER) = u.id) AS p_correct,
                   (SELECT MAX(pl.created_at) FROM practice_logs pl, json_each(pl.unit_ids) je
                     WHERE CAST(je.value AS INTEGER) = u.id) AS last_at
            FROM book_units u
            ORDER BY (u.parent_id IS NOT NULL), u.sort, u.id
            """
        ).fetchall()

    items = []
    for r in rows:
        p_total = r["p_total"] or 0
        p_correct = r["p_correct"] or 0
        wrong_cnt = r["wrong_cnt"] or 0
        practiced = p_total > 0 or wrong_cnt > 0
        items.append(
            {
                "unit_id": r["id"],
                "subject": "english",
                "name": r["name"],
                "word_total": r["total"],
                "practiced": 0,
                "mastered": 0,
                "with_error": wrong_cnt,
                "accuracy": round(p_correct / p_total, 4) if p_total else 0.0,
                "last_practice_at": r["last_at"],
                "text_total": 0,
                "recited": 0,
                "status": (
                    "practiced_with_error"
                    if wrong_cnt > 0
                    else ("practiced_ok" if practiced else "not_started")
                ),
            }
        )
    return ok({"items": items, "grade": None, "semester": None})


@router.get("/api/me/progress")
async def me_progress() -> dict:
    """首页顶部进度概要：总数来自 items，今日/正确率来自 practice_logs。"""
    with read_conn() as conn:
        total_words = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        row = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS t, COALESCE(SUM(correct),0) AS c FROM practice_logs"
        ).fetchone()
        today = conn.execute(
            """
            SELECT COUNT(*) AS sessions,
                   COALESCE(SUM(total),0) AS items,
                   COALESCE(SUM(correct),0) AS correct
            FROM practice_logs WHERE created_at LIKE date('now') || 'T%'
            """
        ).fetchone()

    accuracy = round(row["c"] / row["t"], 4) if row["t"] else 0.0
    return ok(
        {
            "total_words": total_words,
            "practiced_words": 0,
            "mastered_words": 0,
            "total_texts": 0,
            "recited_texts": 0,
            "streak_days": 0,
            "accuracy": accuracy,
            "today": {"sessions": today["sessions"], "items": today["items"], "correct": today["correct"]},
        }
    )


@router.get("/api/me/wrong-words")
async def me_wrong_words() -> dict:
    """错题总数（未移出）。App 首页只读 total。"""
    with read_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM wrong_book WHERE resolved_at IS NULL"
        ).fetchone()[0]
    return ok({"total": total, "items": []})


@router.get("/api/me/wrong-chars")
async def me_wrong_chars() -> dict:
    """语文错字：简化系统不存在，恒 0。"""
    return ok({"total": 0, "items": []})


@router.put("/api/me/profile")
async def me_profile_put() -> dict:
    """年级学期偏好同步：简化系统无载体，空操作（App 侧本地已生效）。"""
    return ok({})


@router.get("/api/units/{unit_id}/words")
async def unit_words(unit_id: int) -> dict:
    """单元词条（学生视角裁剪版）：判分依据（alt_zh/accepted）不下发。"""
    with read_conn() as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.unit_id, i.type, i.en, i.pos, i.zh, i.sort,
                   COALESCE((SELECT SUM(w.wrong_count) FROM wrong_book w
                              WHERE w.item_id = i.id AND w.resolved_at IS NULL), 0) AS wc,
                   EXISTS(SELECT 1 FROM wrong_book w2
                           WHERE w2.item_id = i.id AND w2.resolved_at IS NULL) AS has_wrong
            FROM items i WHERE i.unit_id = ?
            ORDER BY i.sort, i.id
            """,
            (unit_id,),
        ).fetchall()

    words = []
    for r in rows:
        words.append(
            {
                "id": r["id"],
                "unit_id": r["unit_id"],
                "type": r["type"],
                "en": r["en"],
                "cn": r["zh"],
                "phonetic": None,
                "pos": r["pos"] or None,
                "example_en": None,
                "example_cn": None,
                "require_spell": True,
                "order_no": r["sort"],
                "mastery": {
                    "status": "practiced_with_error" if r["has_wrong"] else "not_started",
                    "wrong_count": r["wc"],
                },
            }
        )
    return ok({"words": words})


@router.get("/api/units/{unit_id}/texts")
async def unit_texts(unit_id: int) -> dict:
    """课文列表：简化系统无课文，恒空（背诵配置页据此走词条路径）。"""
    return ok({"texts": []})
