"""语音调用日志 —— WP-BE-06 F16/F17。

每次识别写一条 `asr_logs`：`provider` / `model` / `lang` / `task_type` /
`audio_ms` / `latency_ms` / `status`（`ok`/`empty`/`timeout`/`error`）/ `error_code`，
**带 `session_id` 与 `student_id`** 供成本归因（BE-07 的用量看板就吃这张表）。

两条纪律：

1. **写日志失败绝不能影响语音主流程** —— 表被锁、磁盘满都不该让学生念不出声，
   所以本模块的 `write()` **吞掉所有异常**，只记 logger。
2. `status` 有 CHECK 约束（`ok`/`empty`/`timeout`/`error`），
   **非法值会被 SQLite 拒绝**，所以这里做一次归一化兜底。
"""

from __future__ import annotations

from typing import Any

from app.core.db import utc_now_iso, write_tx
from app.core.logging import get_logger

logger = get_logger(__name__)

VALID_STATUS: frozenset[str] = frozenset({"ok", "empty", "timeout", "error"})


def _norm_status(status: str | None) -> str:
    s = str(status or "").strip().lower()
    return s if s in VALID_STATUS else "error"


class ASRLogService:
    """`asr_logs` 的唯一写入方（BE-07 只读）。"""

    @staticmethod
    def write(
        *,
        provider: str,
        status: str,
        session_id: int | None = None,
        student_id: int | None = None,
        model: str | None = None,
        lang: str | None = None,
        task_type: str | None = None,
        audio_ms: int = 0,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        """写一条识别日志。**任何异常都吞掉**（不影响语音主流程）。

        ⚠️ `asr_logs.session_id` / `student_id` 都有外键约束。若传入的 id 在库里不存在
        （例如客户端传了个过期场次、或测试用假 id），**整条日志会因外键失败而丢失** ——
        成本归因就会凭空少一截。因此这里做一次降级重试：**宁可留下「无归因的记录」，
        也不要整条丢掉**（用量与失败率统计仍然有效）。
        """
        row = (
            int(session_id) if session_id is not None else None,
            int(student_id) if student_id is not None else None,
            str(provider or "unknown"),
            model,
            lang,
            task_type,
            int(audio_ms or 0),
            int(latency_ms) if latency_ms is not None else None,
            _norm_status(status),
            str(error_code) if error_code is not None else None,
            utc_now_iso(),
        )
        sql = """
            INSERT INTO asr_logs
                (session_id, student_id, provider, model, lang, task_type,
                 audio_ms, latency_ms, status, error_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            with write_tx() as conn:
                conn.execute(sql, row)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("写 asr_logs 失败，尝试去掉外键引用重写：%s", exc)

        # 降级重试：把 session_id / student_id 置空，保住其余统计字段
        try:
            with write_tx() as conn:
                conn.execute(sql, (None, None, *row[2:]))
        except Exception as exc:  # noqa: BLE001 - 日志失败不得拖垮语音链路
            logger.error("写 asr_logs 失败（已忽略）：%s", exc)

    @staticmethod
    def recent(limit: int = 50) -> list[dict[str, Any]]:
        """最近若干条（自检 / 烟测用，只读）。"""
        from app.core.db import read_conn

        with read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM asr_logs ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]
