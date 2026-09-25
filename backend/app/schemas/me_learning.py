"""学生端学习相关请求模型 —— WP-BE-04 / 接口设计 §8.3–§8.9。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AppealIn(BaseModel):
    """`POST /api/answers/{id}/appeal`（接口 §8.9）。`reason` 可选，一期可不填。"""

    model_config = ConfigDict(extra="ignore")

    reason: str | None = Field(default=None, max_length=200)
