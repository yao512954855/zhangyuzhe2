"""简化版（S2）请求 / 响应模型 —— WP-A，契约《01-共享契约冻结》§4.1。

枚举取值（契约 §7）在本文件集中定义，API / DB CHECK / 三端 UI 统一用英文值，
禁止出现第二套命名。

校验分层约定：
- **形状类**校验（枚举取值、字段类型）由 Pydantic 完成 → 框架级
  `RequestValidationError` handler 统一转 `10001`；
- **业务类**校验（en 非空去首尾空格、单元存在、判重）由 service 层完成，
  抛 `BizError`（10001 / 30001 / 20003 等）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 枚举常量（契约 §7，全局唯一取值）
# ---------------------------------------------------------------------------

ITEM_TYPES = ("word", "phrase", "sentence")
PRACTICE_MODES = ("listen", "spell_zh2en", "spell_en2zh")


# ---------------------------------------------------------------------------
# 单元（v1.3：UnitCreate/UnitUpdate 移至 api/units.py，含 parent_id 两级约束）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 内容条目
# ---------------------------------------------------------------------------
class ItemCreate(BaseModel):
    unit_id: int
    type: str
    en: str
    pos: str = ""
    zh: str
    alt_zh: str = ""
    sort: int = 0


class ItemUpdate(BaseModel):
    unit_id: int | None = None
    type: str | None = None
    en: str | None = None
    pos: str | None = None
    zh: str | None = None
    alt_zh: str | None = None
    sort: int | None = None


# ---------------------------------------------------------------------------
# 练习（practice，内存会话）
# ---------------------------------------------------------------------------

class PracticeStart(BaseModel):
    unit_id: int
    type: str
    mode: str


class PracticeAnswer(BaseModel):
    session_id: str = Field(min_length=1)
    item_id: int
    mode: str
    answer: str
    elapsed_ms: int = 0


class PracticeSettle(BaseModel):
    session_id: str = Field(min_length=1)
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# 错题本
# ---------------------------------------------------------------------------

class WrongbookAutoAdd(BaseModel):
    item_id: int
    mode: str
