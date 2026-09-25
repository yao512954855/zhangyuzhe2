"""学生 Schema —— WP-BE-02（10a F6 ~ F12）。

**关于 `class_id` 的三态**（最容易写错的地方）：
`PATCH /api/students/{id}` 与归班接口里，`class_id` 有三种语义 ——

| 请求体 | 语义 |
|---|---|
| 字段不出现 | 不修改班级 |
| `"class_id": null` | **移出班级**（`classes` 侧 `ON DELETE SET NULL`，未分班态合法） |
| `"class_id": 3` | 归入 3 号班 |

Pydantic 无法用默认值区分「不传」与「传 null」，因此接口层必须用
`payload.model_fields_set` 判断字段是否出现（见 `app/api/students.py`）。
`AssignClassIn.class_id` 默认为 `None` 是有意的 —— 该接口的语义就是
「设为本值」，传 null 即移出班级。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.core.excel import to_halfwidth
from app.schemas.auth import RegisterSource, Semester, UserStatus


class StudentOut(BaseModel):
    """`GET /api/students` 列表项（接口设计 §4.3）。

    `practice_count` / `accuracy` 为派生字段，口径见下方 `StudentService` 说明：
    - `practice_count`：该生累计答题条数（`answers` 表行数，含 `empty`）
    - `accuracy`：**契约 §8.1** —— (exact + loose + fuzzy) / (总数 − empty)，
      分母为 0 时返回 `0.0`（前端显示为「—」，不显示 0%）
    """

    id: int
    username: str
    real_name: str
    class_id: int | None = None
    class_name: str | None = None
    register_source: RegisterSource = "import"
    status: UserStatus = "active"
    preferred_grade: int | None = None
    preferred_semester: Semester | None = None
    last_login_at: str | None = None
    created_at: str | None = None
    practice_count: int = 0
    accuracy: float = 0.0


class StudentPatchIn(BaseModel):
    """`PATCH /api/students/{id}`（接口设计 §4.8）。

    `status='disabled'` 后该生的旧 token **立即失效** —— 由 BE-01 的
    `get_current_user` 每次查库确认 `status='active'` 保证（10a F12）。
    """

    real_name: str | None = Field(default=None, min_length=1, max_length=20)
    status: UserStatus | None = None
    class_id: int | None = Field(default=None, ge=1)

    @field_validator("real_name")
    @classmethod
    def _v_real_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = to_halfwidth(str(v)).strip()
        if not v:
            raise ValueError("姓名不能为空")
        if len(v) > 20:
            raise ValueError("姓名最多 20 个字")
        return v


class AssignClassIn(BaseModel):
    """`POST /api/students/{id}/assign-class`（接口设计 §4.5）。

    `class_id = null` 表示移出班级（进入「未分班」）。
    """

    class_id: int | None = Field(default=None, ge=1)


class BatchAssignClassIn(BaseModel):
    """`POST /api/students/batch-assign-class`（接口设计 §4.6）。

    上限 500 —— 一个班几十人，500 足够；同时防止单请求放大成超长事务。
    """

    student_ids: list[int] = Field(min_length=1, max_length=500)
    class_id: int | None = Field(default=None, ge=1)


class ResetPasswordIn(BaseModel):
    """`POST /api/students/{id}/reset-password`（接口设计 §4.7）。"""

    new_password: str = Field(min_length=6, max_length=20, description="6–20 位，允许纯数字")


class ImportCommitIn(BaseModel):
    """`POST /api/students/import/commit`（接口设计 §4.4 第二步）。

    `default_password` 缺省时用 `DEFAULT_STUDENT_PASSWORD`（`123456`）。

    ⚠️ **默认密码只作用于「新建」的账号。** 对已存在的账号（重复导入 → 更新）
    不会重置密码，除非该行在 Excel 里显式填了「初始密码」列 ——
    否则一次误操作的重导就会把全班密码刷回弱口令，属安全事故。
    """

    preview_token: str = Field(min_length=1)
    default_password: str | None = Field(default=None, min_length=6, max_length=20)
