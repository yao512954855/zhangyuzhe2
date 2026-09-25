"""游戏化激励 —— WP-2K（二期《01-共享契约冻结》§15 / 《50-二期工作包拆解》§3）。

---

## 设计原则（契约硬约束）

1. **读时惰性刷新**：`snapshot()` 只根据现有数据（`answers` / `sessions` /
   `word_mastery` / `recitations`）**实时推导**积分与徽章；由路由层在 `GET` 里
   upsert 到 `user_points` / `user_badges`（本包是这两张表的**唯一写入方**，
   契约 §15.1 / §15.6）。
2. **徽章必须由数据真实推导，禁止编造**（契约禁止清单「禁桩/假数据」）。
   本模块不写任何业务假数据，徽章是否达成完全由 `_Stats` 的字段阈值判定。
3. **积分 / 徽章规则一律写成具名常量表**，不硬编码散布。所有"建议值"集中在
   文件顶部 `POINTS_*` 与 `BADGE_CATALOG`，便于后续调参。
4. **只读边界**：本模块读 `answers` / `sessions` / `word_mastery` / `recitations`，
   **不写**这些表（写入方唯一性，契约 §4.2 / §15.1）。只写 `user_points` /
   `user_badges`（通过 `apply_refresh`，在调用方提供的写事务内执行）。

## 复用约定

- 正确率口径不在本模块（本包不展示正确率）。
- 连续天数（`streak_days`）复用 `MasteryService.streak_days()`（已在个人报告里用，
  本模块不另起一份口径，避免漂移）。练习天以**北京时间**切天，复用 `stats_service._BJ`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.core.db import read_conn
from app.services.mastery_service import MasteryService
from app.services.stats_service import _BJ, beijing_day


# ===========================================================================
# 积分规则（具名常量 —— 单位：分；均为"建议值"，调参只改这里）
# ===========================================================================

#: 每答对一题（exact / loose / fuzzy）得 10 分。
POINTS_PER_CORRECT = 10

#: 每掌握一个词条（word_mastery.mastered_at 非空）得 50 分。
POINTS_PER_MASTERED_WORD = 50

#: 每完成一次背诵（recitations.finish_reason='completed'）得 30 分。
POINTS_PER_RECITATION = 30

#: 每完成一场练习（sessions.status='finished'）得 20 分。
POINTS_PER_FINISHED_SESSION = 20

#: 连续天数奖励：最终积分 += `streak_days * 5`（鼓励坚持）。
STREAK_BONUS_PER_DAY = 5


# ===========================================================================
# 徽章目录（具名常量表）
# ===========================================================================


@dataclass(frozen=True)
class BadgeDef:
    """徽章定义（冻结的"徽章规则表"条目）。

    每个徽章的达成条件完全由 `_Stats` 的某个字段阈值决定 —— 不依赖任何
    未写入数据库的状态，保证"徽章真实从数据推导"。
    """

    code: str
    name: str
    desc: str
    #: 触发该徽章的 `_Stats` 字段名（仅作文档/校验用途；判定见 `predicate`）。
    metric: str
    #: 达成阈值（与 `metric` 配套，用于文档与可能的阈值校验）。
    threshold: int
    predicate: Callable[["_Stats"], bool]


@dataclass
class _Stats:
    """某学生可推导的聚合量（全部来自现有表，未做任何编造）。"""

    answer_count: int            # 总答题条数（含空识别）
    correct_count: int           # 正确条数（exact + loose + fuzzy）
    mastered_words: int          # 已掌握词条数（mastery_at 非空）
    recitation_count: int        # 完成背诵次数（finish_reason='completed'）
    finished_session_count: int  # 完成场次数（status='finished'）
    streak_days: int             # 连续练习天数（复用 MasteryService）
    practice_days: int           # 有练习的不同天数（北京时间）


@dataclass
class BadgeState:
    """某徽章在某学生身上的达成状态。"""

    code: str
    name: str
    earned: bool
    earned_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "name": self.name,
            "earned": self.earned,
        }
        if self.earned and self.earned_at is not None:
            data["earned_at"] = self.earned_at
        return data


#: 徽章规则表（WP-2K 验收报告要原样列出）。
#: 顺序即"徽章墙"展示顺序；增删徽章只改这里。
BADGE_CATALOG: tuple[BadgeDef, ...] = (
    BadgeDef(
        "first_practice", "初次练习", "完成第一道题",
        "answer_count", 1, lambda s: s.answer_count >= 1,
    ),
    BadgeDef(
        "ten_correct", "十全十美", "累计答对 10 题",
        "correct_count", 10, lambda s: s.correct_count >= 10,
    ),
    BadgeDef(
        "hundred_correct", "百发百中", "累计答对 100 题",
        "correct_count", 100, lambda s: s.correct_count >= 100,
    ),
    BadgeDef(
        "first_mastered", "初露锋芒", "掌握第一个单词",
        "mastered_words", 1, lambda s: s.mastered_words >= 1,
    ),
    BadgeDef(
        "ten_mastered", "小有成就", "掌握 10 个单词",
        "mastered_words", 10, lambda s: s.mastered_words >= 10,
    ),
    BadgeDef(
        "first_recite", "朗朗上口", "完成第一次背诵",
        "recitation_count", 1, lambda s: s.recitation_count >= 1,
    ),
    BadgeDef(
        "first_session", "踏上征程", "完成第一场练习",
        "finished_session_count", 1, lambda s: s.finished_session_count >= 1,
    ),
    BadgeDef(
        "fifty_sessions", "勤学不辍", "完成 50 场练习",
        "finished_session_count", 50, lambda s: s.finished_session_count >= 50,
    ),
    BadgeDef(
        "streak_3", "三日之工", "连续练习 3 天",
        "streak_days", 3, lambda s: s.streak_days >= 3,
    ),
    BadgeDef(
        "streak_7", "持之以恒", "连续练习 7 天",
        "streak_days", 7, lambda s: s.streak_days >= 7,
    ),
)


@dataclass
class GamifySnapshot:
    """一次积分 / 徽章推导结果（契约 §15.5 的 `GamifySnapshot`）。"""

    points: int
    streak_days: int
    badges: list[BadgeState]

    def to_dict(self) -> dict[str, Any]:
        return {
            "points": self.points,
            "streak_days": self.streak_days,
            "badges": [b.to_dict() for b in self.badges],
        }


# ===========================================================================
# 推导
# ===========================================================================


def compute_points(stats: _Stats) -> int:
    """根据 `_Stats` 累加积分（所有分量均为非负，结果必 ≥ 0）。

    集中实现，避免"公式在多处重写导致口径漂移" —— 测试也复用它做对账。
    """
    return (
        stats.correct_count * POINTS_PER_CORRECT
        + stats.mastered_words * POINTS_PER_MASTERED_WORD
        + stats.recitation_count * POINTS_PER_RECITATION
        + stats.finished_session_count * POINTS_PER_FINISHED_SESSION
        + stats.streak_days * STREAK_BONUS_PER_DAY
    )


def _compute_stats(conn, student_id: int) -> _Stats:
    """从现有表聚合出 `_Stats`（**严格带 `student_id` 过滤**）。

    ⚠️ 这里每一个聚合查询都必须 `WHERE student_id = ?`，否则会发生跨学生串数据
    （学生隔离的安全漏洞，且会让反向验证用例转红）。
    """
    sid = int(student_id)

    ans = conn.execute(
        """
        SELECT COUNT(*) AS answer_count,
               SUM(CASE WHEN match_type IN ('exact','loose','fuzzy')
                        THEN 1 ELSE 0 END) AS correct_count
        FROM answers
        WHERE student_id = ?
        """,
        (sid,),
    ).fetchone()

    mastered = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM word_mastery
        WHERE student_id = ? AND mastered_at IS NOT NULL
        """,
        (sid,),
    ).fetchone()

    recit = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM recitations
        WHERE student_id = ? AND finish_reason = 'completed'
        """,
        (sid,),
    ).fetchone()

    finished = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM sessions
        WHERE student_id = ? AND status = 'finished'
        """,
        (sid,),
    ).fetchone()

    # 练习天（北京时间）用于连续天数计算。
    days = [
        str(r["d"])
        for r in conn.execute(
            """
            SELECT DISTINCT date(datetime(created_at, ?)) AS d
            FROM answers
            WHERE student_id = ?
            """,
            (_BJ, sid),
        ).fetchall()
    ]
    streak = MasteryService.streak_days(days, beijing_day(0))

    return _Stats(
        answer_count=int(ans["answer_count"] or 0),
        correct_count=int(ans["correct_count"] or 0),
        mastered_words=int(mastered["n"] or 0),
        recitation_count=int(recit["n"] or 0),
        finished_session_count=int(finished["n"] or 0),
        streak_days=streak,
        practice_days=len(days),
    )


def snapshot(conn, student_id: int) -> GamifySnapshot:
    """根据现有数据推导积分与徽章（**只读**，不写任何表）。

    参数
    ----
    conn: 连接（由调用方提供；可处于只读或写事务上下文中，本函数只 SELECT）。
    student_id: 必须是调用方已鉴权的当前学生。

    返回
    ----
    积分（`points`）、连续天数（`streak_days`）、完整徽章墙（`badges`：
    含未达成项，`earned=False`）。`earned_at` 由 `apply_refresh` 回填。
    """
    stats = _compute_stats(conn, int(student_id))
    points = compute_points(stats)
    badges = [
        BadgeState(code=d.code, name=d.name, earned=bool(d.predicate(stats)))
        for d in BADGE_CATALOG
    ]
    return GamifySnapshot(points=points, streak_days=stats.streak_days, badges=badges)


def apply_refresh(conn, student_id: int, snap: GamifySnapshot) -> dict[str, str]:
    """读时惰性刷新：把推导结果写回 `user_points` / `user_badges`。

    - `user_points`：按 `student_id` upsert（幂等，不会多行）。
    - `user_badges`：只**插入**已达成徽章（`INSERT OR IGNORE` + 唯一索引保证幂等）；
      不删除未达成的徽章，因为徽章是**单调**的（达成后不会因数据波动而收回）。

    必须在调用方提供的**写事务**（`write_tx()`）内调用。

    返回：`{badge_code: awarded_at}`，供接口回填 `earned_at`。
    """
    from app.core.db import utc_now_iso

    sid = int(student_id)
    now = utc_now_iso()

    conn.execute(
        """
        INSERT INTO user_points (student_id, points, streak_days, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(student_id) DO UPDATE SET
            points       = excluded.points,
            streak_days  = excluded.streak_days,
            updated_at   = excluded.updated_at
        """,
        (sid, snap.points, snap.streak_days, now),
    )

    for b in snap.badges:
        if b.earned:
            conn.execute(
                "INSERT OR IGNORE INTO user_badges (student_id, badge_code) VALUES (?, ?)",
                (sid, b.code),
            )

    awarded = {
        str(r["badge_code"]): str(r["awarded_at"])
        for r in conn.execute(
            "SELECT badge_code, awarded_at FROM user_badges WHERE student_id = ?",
            (sid,),
        ).fetchall()
    }
    return awarded
