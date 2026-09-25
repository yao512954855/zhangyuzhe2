"""班级 Schema —— WP-BE-02（10a F4 ~ F5，契约 §14.1 第 1 条）。

> ⚠️ **文件名偏离说明（需回写 10a 文档）**：10a 的独占文件清单写的是
> `backend/app/schemas/class.py`，但 **`class` 是 Python 保留字，不能作为模块名导入**
> （`from app.schemas.class import ...` 是语法错误）。故改名为 `classes.py`，
> 与 `app/api/classes.py` 命名保持一致。这是纯机械原因，不影响任何契约。

字段与 `classes` 表一一对应（契约 §2：JSON 字段名不做驼峰转换）。
`student_count` 不是表字段，是列表查询时聚合出来的派生值。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.core.excel import to_halfwidth
from app.schemas.auth import ClassStatus, Semester


class ClassOut(BaseModel):
    """`GET /api/classes` 列表项（接口设计 §4.1）。"""

    id: int
    name: str
    grade: int
    semester: Semester
    school_year: str | None = None
    remark: str | None = None
    status: ClassStatus = "active"
    student_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class ClassCreateIn(BaseModel):
    """`POST /api/classes`（接口设计 §4.2）。

    同名冲突由 `uk_classes_name`（**仅对 `status='active'` 生效**的部分唯一索引）兜底：
    归档班级不占用名字，因此同名冲突返回 `10001` 且提示写清「已存在同名班级」。
    """

    name: str = Field(min_length=1, max_length=30)
    grade: int = Field(ge=1, le=9, description="1–9（小学 1–6 + 初中 7–9）")
    semester: Semester
    school_year: str | None = Field(default=None, max_length=20, description="如 2026-2027")
    remark: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        v = to_halfwidth(str(v or "")).strip()
        if not v:
            raise ValueError("班级名称不能为空")
        return v

    @field_validator("school_year", "remark")
    @classmethod
    def _v_optional_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = to_halfwidth(str(v)).strip()
        return v or None


class ClassPatchIn(BaseModel):
    """`PATCH /api/classes/{id}` —— 改名称 / 备注 / 归档（契约 §14.1 第 1 条，一期用本接口）。"""

    name: str | None = Field(default=None, min_length=1, max_length=30)
    remark: str | None = Field(default=None, max_length=200)
    status: ClassStatus | None = None

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = to_halfwidth(str(v)).strip()
        if not v:
            raise ValueError("班级名称不能为空")
        return v

    @field_validator("remark")
    @classmethod
    def _v_remark(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = to_halfwidth(str(v)).strip()
        return v or None
