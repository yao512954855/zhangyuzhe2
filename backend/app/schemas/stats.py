"""统计看板与申诉复核的入参模型 —— WP-BE-07。

本模块只放**请求体**（`Body`）。查询参数（`date_from` / `status` / 分页等）走
`Request.query_params` 手工解析，理由：
- 分页已由 `core.pagination.parse_pagination` 统一处理，重复建模会让「上限 100」这类
  规则出现两处实现；
- 日期参数的错误提示要求**带字段名**（`10001` + 「date_from 格式应为 YYYY-MM-DD」），
  手工校验比 Pydantic 的默认英文 message 更符合 UI 规范的文案要求。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AppealReviewIn(BaseModel):
    """`POST /api/appeals/{id}/review` 请求体（10b F5）。"""

    result: Literal["upheld", "rejected"] = Field(
        ..., description="复核结论：upheld=申诉成立 / rejected=维持原判"
    )
    remark: str | None = Field(
        default=None,
        max_length=200,
        description="复核备注（可选，最长 200 字）",
    )


class ExportQuery(BaseModel):
    """`GET /api/stats/export` 的查询参数（F11）。"""

    type: Literal["class", "student"] = Field(..., description="导出对象类型")
    id: int = Field(..., ge=1, description="班级 ID 或学生 ID")  # noqa: A002 - 与接口字段同名
