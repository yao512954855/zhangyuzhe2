"""英语三档提示服务（WP-2J / 契约 §15.4）。

**为什么是独立端点、不回写组卷响应**：一期裁决「英语三档提示划归语文」的根因是
契约 §9.1 **组卷响应绝不返回答案**。二期正确做法是新增本独立端点，答案只在
「学生主动请求提示 + 按本题失败次数门控」时下发 —— 任何情况下都不允许为提示
去改组卷响应（§13.4 / §15.6 红线）。

**门控（§15.4，⛔ 不许偷懒）**：

| 档 | 内容 | 门控 |
|---|---|---|
| 1 | 占位框数量 | 立即（失败 0 次即可） |
| 2 | 首字母 | 本题失败 ≥ 1 次 |
| 3 | 完整答案 | 本题失败 ≥ 2 次 |

越档 → `70006`（HINT_EXHAUSTED）；`en2cn` 题型 → `70007`（HINT_NOT_ALLOWED）。

**失败次数权威来源**：后端 `answers` 表按 `(session_id, word_id)` 统计历史失败
（`is_correct = 0` 且 `match_type <> 'empty'`）。绝不信任客户端传入的失败数。
"""

from __future__ import annotations

from typing import Any

from app.core.db import read_conn
from app.core.errors import BizError, ErrorCode
from app.services.judge.normalize import normalize_letters


class HintService:
    """英语提示的纯读服务（不产生写操作，答案来源恒为库）。"""

    #: 题型 → 是否提供提示。仅 `cn2en` 支持三档提示，`en2cn` 直接拒绝。
    _HINTABLE_MODES = ("cn2en",)

    @classmethod
    def english_hint(
        cls,
        *,
        student_id: int,
        session_id: int,
        item_idx: int,
        word_id: int,
        mode: str,
        current_level: int,
    ) -> dict[str, Any]:
        """取一次英语提示，返回 `{level, hint, exhausted}`。

        Args:
            student_id: 当前登录学生（鉴权主体）。
            session_id: 场次 id（路径 `{sid}`）。
            item_idx: 题序（路径 `{idx}`，仅用于契约路径，权威标识靠 `word_id`）。
            word_id: 本题词条 id（来自组卷响应 `items[].word_id`，客户端持有）。
            mode: 本题题型（`cn2en` / `en2cn`），来自组卷响应。
            current_level: 客户端当前已持有的最高档位（0 = 尚未取过提示）。

        Raises:
            BizError(NOT_FOUND): 场次不存在 / 不属于当前学生 / 词条不属于本场单元
                —— 三者同码，不暴露资源存在性（A3 安全口径，契约 §15.2）。
            BizError(HINT_NOT_ALLOWED): `en2cn` 题型无提示（§15.4）。
            BizError(HINT_EXHAUSTED): 请求档位超过当前失败次数允许的上限（越档）。
        """
        if int(item_idx) < 1:
            raise BizError(ErrorCode.PARAM_INVALID, "题序不合法")

        # 1) 校验场次归属（越权与不存在同码 10004）
        with read_conn() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (int(session_id),)
            ).fetchone()
            if session is None:
                raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")
            session = dict(session)
            if int(session["student_id"]) != int(student_id):
                # 越权：不暴露「存在但不属于你」
                raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")

            # 2) 校验词条属于本场单元（防跨单元取提示）
            word = conn.execute(
                "SELECT * FROM words WHERE id = ?", (int(word_id),)
            ).fetchone()
            if word is None:
                raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")
            word = dict(word)
            if int(word["unit_id"]) != int(session["unit_id"]):
                raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")

            # 3) 题型门控：`en2cn` 无提示
            if str(mode) not in cls._HINTABLE_MODES:
                raise BizError(ErrorCode.HINT_NOT_ALLOWED, "这道题不能使用提示")

            # 4) 本题历史失败次数（权威来源：后端 answers 表）
            failures = cls._count_failures(conn, session_id, word_id)

        # 5) 失败次数 → 允许的最高档位
        max_allowed = cls._max_level_for_failures(failures)

        # 6) 越档判定
        requested = int(current_level) + 1
        if requested > max_allowed:
            raise BizError(ErrorCode.HINT_EXHAUSTED, "提示已经用完了")

        # 7) 生成该档提示内容（答案来源恒为 words 表）
        hint_text = cls._build_hint(word, requested)

        # 8) 是否「当前门控下」已无更高档可升
        exhausted = requested >= max_allowed

        return {"level": requested, "hint": hint_text, "exhausted": exhausted}

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _count_failures(conn: Any, session_id: int, word_id: int) -> int:
        """本题历史失败次数。

        `is_correct = 0` 且 `match_type <> 'empty'`：
        - `wrong` / `timeout` 计入分母（§8.1）；
        - `empty`（空录音）不计错、不计入分母。
        """
        row = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM answers
            WHERE session_id = ?
              AND word_id = ?
              AND is_correct = 0
              AND match_type <> 'empty'
            """,
            (int(session_id), int(word_id)),
        ).fetchone()
        return int(row["c"]) if row is not None else 0

    @staticmethod
    def _max_level_for_failures(failures: int) -> int:
        """门控映射（§15.4）。"""
        if failures <= 0:
            return 1
        if failures == 1:
            return 2
        return 3

    @staticmethod
    def _build_hint(word: dict[str, Any], level: int) -> str:
        """按档位生成提示内容，答案来源恒为 `words` 表，绝不凭参数回显。"""
        en = str(word.get("en") or "")
        norm = normalize_letters(en)

        if level <= 1:
            # 档 1：占位框数量（字母数）
            return f"答案共 {len(norm)} 个字母"

        if level == 2:
            # 档 2：首字母
            first = norm[0] if norm else ""
            return f"首字母：{first.upper()}"

        # 档 3：完整答案
        accepted = word.get("accepted")
        if accepted:
            return f"{en}（也可写：{accepted}）"
        return en
