"""导入 Schema —— WP-BE-03（10a F12 ~ F15，接口设计 §6.2 ~ §6.5）。

两步式导入的第二步入参。第一步（preview）只解析校验、**不落库**，
把结果存进 `core/preview_store`（BE-01 提供，**BE-02 / BE-03 共用**，
契约 §14.1 第 4 条），返回 `preview_token`；第二步凭 token 入库。

⚠️ token 是**一次性**的：commit 走 `preview_store.pop()`，
同一个 token 提交两次，第二次会拿到「已过期」——
这是刻意的（防重复提交），不是 bug。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

#: 重复词条的处理策略（接口设计 §6.3）
DuplicateStrategy = Literal["overwrite", "skip"]


class WordsImportCommitIn(BaseModel):
    """`POST /api/import/words/commit`（接口设计 §6.3，10a F13）。"""

    preview_token: str = Field(min_length=1)

    create_missing_units: bool = Field(
        default=True,
        description="单元不存在时是否自动建。false 且文件含新单元 → 明确报错，不建单元不落词条（U3）",
    )
    on_duplicate: DuplicateStrategy = Field(
        default="overwrite",
        description="overwrite 覆盖 / skip 跳过。判重键是 (unit_id, en)，对应 uk_words",
    )


class TextScopeIn(BaseModel):
    """课文 commit 时回传的背诵范围（接口设计 §6.5）。

    `row` 对应 preview 时返回的 Excel 行号 —— 用它把前端的调整**精确映射回**那份文件，
    不用数组下标（下标会因「部分行非法被剔除」而错位，这是最容易写错的地方）。
    """

    row: int = Field(ge=1)
    recite_scope: list[int] = Field(default_factory=list, description="背诵段落下标；空数组表示全部")

    @model_validator(mode="after")
    def _v_scope(self) -> "TextScopeIn":
        if len(self.recite_scope) > 200:
            raise ValueError("背诵范围段落数过多")
        if any(i < 0 for i in self.recite_scope):
            raise ValueError("背诵段落下标不能为负")
        # 去重 + 升序：前端勾选框的顺序不保证，存库前统一
        self.recite_scope = sorted(set(self.recite_scope))
        return self


class TextsImportCommitIn(BaseModel):
    """`POST /api/import/texts/commit`（接口设计 §6.5，10a F15）。"""

    preview_token: str = Field(min_length=1)
    texts: list[TextScopeIn] = Field(default_factory=list, description="留空表示全部按 preview 的默认范围入库")
