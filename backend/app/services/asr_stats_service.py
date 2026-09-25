"""ASR 用量统计 —— WP-BE-07 F8（需求 §9.6 成本控制）。

数据来源**两张表拼起来**：

| 表 | 覆盖范围 | 特点 |
|---|---|---|
| `asr_logs` | 最近 90 天明细 | 有 `latency_ms` / `status` 逐条可算，最新 |
| `asr_daily_stats` | 已被日聚合（F9）吞掉的更早的天 | 只有汇总值，没有逐条样本 |

### 合并规则（**必须说清，否则很容易把同一天算两遍**）

对每个 `(stat_date, provider)`：

- 明细里有 → **用明细**；
- 明细里没有但汇总里有 → 用汇总。

同一天**不会**既有明细又有汇总：F9 只把「超出保留期」的天聚合进 `asr_daily_stats`
并从 `asr_logs` 删除，所以两边天然互斥。为防手工改过 `keep_days` 后出现重叠，
每行都带 `source` 字段（`detail` / `daily_stats`），对不上账时一眼能看出来。

### 失败率口径

`失败率 = (error + timeout) / 调用总数`。**`empty` 不算失败** ——
它是「学生没出声/麦克风没采到」，属用户侧，不是服务故障。
把它算进失败率会让一次课堂嘈杂直接触发运维告警（模块 docstring 见
`stats_service` §1.4 的同一约定）。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.core.db import read_conn
from app.services.stats_service import _BJ, normalise_range

#: 明细保留天数（与 F9 的 `KEEP_DAYS` 必须一致）
DETAIL_KEEP_DAYS = 90

#: `asr_logs.status` 里算「失败」的取值
FAILED_STATUSES = ("error", "timeout")


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator > 0 else 0.0


def _row_from_detail(r: sqlite3.Row) -> dict[str, Any]:
    calls = int(r["call_count"] or 0)
    ok = int(r["ok_count"] or 0)
    empty = int(r["empty_count"] or 0)
    failed = int(r["failed_count"] or 0)
    latency_total = r["latency_sum"]
    return {
        "stat_date": str(r["stat_date"]),
        "provider": r["provider"],
        "call_count": calls,
        "total_audio_ms": int(r["total_audio_ms"] or 0),
        "avg_latency_ms": int(round(latency_total / ok)) if ok and latency_total else None,
        "ok_count": ok,
        "empty_count": empty,
        "failed_count": failed,
        "failure_rate": _rate(failed, calls),
        "source": "detail",
    }


def _row_from_daily(r: sqlite3.Row) -> dict[str, Any]:
    calls = int(r["call_count"] or 0)
    ok = int(r["ok_count"] or 0)
    empty = int(r["empty_count"] or 0)
    failed = int(r["error_count"] or 0)
    return {
        "stat_date": str(r["stat_date"]),
        "provider": r["provider"],
        "call_count": calls,
        "total_audio_ms": int(r["total_audio_ms"] or 0),
        "avg_latency_ms": int(r["avg_latency_ms"]) if r["avg_latency_ms"] is not None else None,
        "ok_count": ok,
        "empty_count": empty,
        "failed_count": failed,
        "failure_rate": _rate(failed, calls),
        "source": "daily_stats",
    }


class AsrStatsService:
    """F8 ASR 用量统计（只读）。"""

    @staticmethod
    def usage(
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """按天 + 按厂商的用量、平均延迟、失败率。

        Args:
            provider: 只看某个厂商（如 `mock` / `aliyun`），留空看全部。
        """
        start, end = normalise_range(date_from, date_to)
        prov = (provider or "").strip() or None

        prov_clause = " AND provider = ?" if prov else ""
        prov_params: list[Any] = [prov] if prov else []

        with read_conn() as conn:
            detail_rows = conn.execute(
                f"""
                SELECT date(datetime(created_at, ?)) AS stat_date,
                       provider,
                       COUNT(*)                        AS call_count,
                       SUM(audio_ms)                   AS total_audio_ms,
                       SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                       SUM(CASE WHEN status = 'empty' THEN 1 ELSE 0 END) AS empty_count,
                       SUM(CASE WHEN status IN ('error','timeout') THEN 1 ELSE 0 END)
                           AS failed_count,
                       SUM(CASE WHEN status = 'ok' AND latency_ms IS NOT NULL
                                THEN latency_ms ELSE 0 END) AS latency_sum
                FROM asr_logs
                WHERE date(datetime(created_at, ?)) BETWEEN ? AND ?
                  {prov_clause}
                GROUP BY stat_date, provider
                ORDER BY stat_date, provider
                """,
                [_BJ, _BJ, start, end, *prov_params],
            ).fetchall()

            daily_rows = conn.execute(
                f"""
                SELECT stat_date, provider, call_count, total_audio_ms,
                       avg_latency_ms, ok_count, empty_count, error_count
                FROM asr_daily_stats
                WHERE stat_date BETWEEN ? AND ?
                  {prov_clause}
                ORDER BY stat_date, provider
                """,
                [start, end, *prov_params],
            ).fetchall()

        merged: dict[tuple[str, str], dict[str, Any]] = {}
        # 先放汇总，再用明细覆盖 —— 「明细优先」的写法（而不是先明细再判断）
        for r in daily_rows:
            key = (str(r["stat_date"]), str(r["provider"]))
            merged[key] = _row_from_daily(r)
        for r in detail_rows:
            key = (str(r["stat_date"]), str(r["provider"]))
            merged[key] = _row_from_detail(r)

        daily = [merged[k] for k in sorted(merged)]

        # 按厂商汇总
        by_provider: dict[str, dict[str, Any]] = {}
        for row in daily:
            p = row["provider"]
            acc = by_provider.setdefault(
                p,
                {
                    "provider": p,
                    "call_count": 0,
                    "total_audio_ms": 0,
                    "ok_count": 0,
                    "empty_count": 0,
                    "failed_count": 0,
                    "_latency_weighted": 0.0,
                },
            )
            acc["call_count"] += row["call_count"]
            acc["total_audio_ms"] += row["total_audio_ms"]
            acc["ok_count"] += row["ok_count"]
            acc["empty_count"] += row["empty_count"]
            acc["failed_count"] += row["failed_count"]
            if row["avg_latency_ms"] is not None and row["ok_count"]:
                # 以 ok_count 为权重近似整体均值：明细行拿不到「延迟样本数」，
                # 而 ok 行才可能带 latency_ms，用 ok_count 当权重是当前数据下最优的近似。
                acc["_latency_weighted"] += row["avg_latency_ms"] * row["ok_count"]

        provider_list: list[dict[str, Any]] = []
        for p in sorted(by_provider):
            acc = by_provider[p]
            weighted = acc.pop("_latency_weighted")
            acc["avg_latency_ms"] = (
                int(round(weighted / acc["ok_count"])) if acc["ok_count"] else None
            )
            acc["failure_rate"] = _rate(acc["failed_count"], acc["call_count"])
            provider_list.append(acc)

        totals = {
            "call_count": sum(r["call_count"] for r in daily),
            "total_audio_ms": sum(r["total_audio_ms"] for r in daily),
            "ok_count": sum(r["ok_count"] for r in daily),
            "empty_count": sum(r["empty_count"] for r in daily),
            "failed_count": sum(r["failed_count"] for r in daily),
            "avg_latency_ms": (
                int(round(sum(r["avg_latency_ms"] * r["ok_count"]
                              for r in daily
                              if r["avg_latency_ms"] is not None and r["ok_count"])
                          / sum(r["ok_count"] for r in daily if r["ok_count"])))
                if any(r["ok_count"] for r in daily)
                else None
            ),
        }
        totals["failure_rate"] = _rate(totals["failed_count"], totals["call_count"])
        totals["total_audio_minutes"] = round(totals["total_audio_ms"] / 60000, 1)

        return {
            "date_from": start,
            "date_to": end,
            "provider": prov,
            "detail_keep_days": DETAIL_KEEP_DAYS,
            "daily": daily,
            "by_provider": provider_list,
            "totals": totals,
        }
