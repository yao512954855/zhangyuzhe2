"""个人正确率趋势 —— WP-2A（二期工作包拆解 §3 WP-2A、共享契约 §15.5）。

---

## 口径（与 BE-07 看板**完全一致**，不得另起炉灶）

- **天按北京时间切**：`date(datetime(created_at, ?))` 绑定 `_BJ = "+8 hours"`
  （复用 `stats_service._BJ` 这一个唯一出处，**不另写** `+8 hour` / `Asia/Shanghai`）。
- **正确率为全项目唯一口径**：转发 `UserService._accuracy()`（经由 `StatsService.calc_accuracy`），
  本模块**不含第二份公式实现**。契约 §15.5 明文：「趋势聚合转发它，不得重写」。
- **缺的天补零**：与 `StatsService._class_trend` 同样处理，否则折线图把两点直连、视觉骗人。
- **只回自己**（`WHERE student_id = ?`）：越权看别人是安全漏洞，见后端测试的反向验证。

## 只读边界

本模块只读 `answers`（不写任何业务表）。趋势是实时聚合，不建物化表（二期 §2.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.services.stats_service import StatsService, _BJ, beijing_day, day_series

#: 个人趋势默认天数（含今天）。
DEFAULT_TREND_DAYS = 30

#: 单名学生趋势最多返回多少天（防客户端拉 3 年数据把图表画爆；与管理端 MAX_TREND_DAYS 同思路）。
MAX_TREND_DAYS = 365


@dataclass
class TrendPoint:
    """单日正确率趋势点（契约 §15.5 的响应形状）。"""

    date: str
    accuracy: float
    answered: int
    correct: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "accuracy": self.accuracy,
            "answered": self.answered,
            "correct": self.correct,
        }


def accuracy_trend(
    conn, student_id: int, days: int
) -> list[TrendPoint]:
    """聚合某学生最近 `days` 天（**含今天**）的逐日正确率趋势。

    参数
    ----
    conn: 只读连接（由调用方提供，遵循本项目的 `read_conn` 事务边界）。
    student_id: 目标学生 id（**必须**是调用方已鉴权的当前学生，否则越权）。
    days: 天数（含今天），调用方应已校验 `1 <= days <= MAX_TREND_DAYS`。

    返回
    ----
    `list[TrendPoint]`，按日期升序，**缺失天补零**。

    - `answered` = 当天该学生的答题条数（`COUNT(*)`，含 `empty`）；
    - `correct` = 当天正确条数（`exact + loose + fuzzy`）；
    - `accuracy` = `UserService._accuracy(correct, counted)`，`counted` 为剔除 `empty` 后的有效条数，
      分母 0 → `0.0`（与 `student_stats` / 班级概览同一口径）。
    """
    sid = int(student_id)
    n = int(days)

    # 区间闭区间：今天 + 前 (n-1) 天。
    end = beijing_day(0)
    start = _shift(end, -(n - 1))
    series = day_series(start, end)

    buckets: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        """
        SELECT date(datetime(created_at, ?)) AS d,
               COUNT(*)                                              AS answered,
               SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted,
               SUM(CASE WHEN match_type IN ('exact','loose','fuzzy')
                        THEN 1 ELSE 0 END)                           AS correct
        FROM answers
        WHERE student_id = ?
          AND date(datetime(created_at, ?)) BETWEEN ? AND ?
        GROUP BY d
        """,
        # `_BJ` 两处都用同一个常量：切天偏移量只有这一处出处。
        [_BJ, sid, _BJ, start, end],
    ).fetchall():
        buckets[str(row["d"])] = {
            "answered": int(row["answered"] or 0),
            "counted": int(row["counted"] or 0),
            "correct": int(row["correct"] or 0),
        }

    points: list[TrendPoint] = []
    for day in series:
        b = buckets.get(
            day, {"answered": 0, "counted": 0, "correct": 0}
        )
        points.append(
            TrendPoint(
                date=day,
                accuracy=StatsService.calc_accuracy(b["correct"], b["counted"]),
                answered=b["answered"],
                correct=b["correct"],
            )
        )
    return points


def _shift(day: str, delta: int) -> str:
    """`day` 偏移 `delta` 天，返回 `YYYY-MM-DD`。"""
    return (date.fromisoformat(day) + timedelta(days=delta)).isoformat()
