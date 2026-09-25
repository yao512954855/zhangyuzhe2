"""练习流程的请求模型 —— WP-BE-04 / 接口设计 §9.1–§9.3。

响应统一用 `ok()` 包装 dict（字段少、且要与契约示例逐字对齐），
这里只放**需要校验的请求体**。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Scope = Literal["unit", "lesson", "paragraph", "range", "wrong", "unmastered"]
CountMode = Literal["all", "first_n", "random_n"]
OrderMode = Literal["sequential", "random", "wrong_first", "reverse"]
PracticeMode = Literal["cn2en", "en2cn", "mixed"]


class StrategyIn(BaseModel):
    """出题策略（接口 §9.1）。原样存进 `sessions.strategy` 作为快照。

    ⚠️ 策略以后改了也**不影响历史统计口径**，所以快照必须是原样 JSON。
    """

    model_config = ConfigDict(extra="ignore")

    scope: Scope = "unit"
    scope_detail: dict[str, Any] | None = None
    count: int = Field(default=0, ge=0, description="0 表示全部")
    count_mode: CountMode = "all"
    order: OrderMode = "sequential"
    mode: PracticeMode = "cn2en"
    time_limit_ms: int = Field(default=0, ge=0, description="0 表示不限时")

    @field_validator("count_mode", mode="before")
    @classmethod
    def _normalize_count_mode(cls, value: Any) -> Any:
        """容错：接口 §9.1 的示例写成 `"random"`，字段表写 `"random_n"`。

        以字段表为准（`random_n`），同时把示例里的旧写法接住 ——
        App 端早期版本可能已经发出 `random`。
        """
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "random":
                return "random_n"
            return normalized
        return value

    @field_validator("scope_detail")
    @classmethod
    def _check_scope_detail(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return value


class SessionCreateIn(BaseModel):
    """`POST /api/sessions`（接口 §9.1）。"""

    model_config = ConfigDict(extra="ignore")

    unit_id: int
    content_type: Literal["english", "chinese"] = "english"
    strategy: StrategyIn = Field(default_factory=StrategyIn)


class AnswerSubmitIn(BaseModel):
    """`POST /api/sessions/{id}/answers`（接口 §9.2）。"""

    model_config = ConfigDict(extra="ignore")

    word_id: int
    mode: Literal["cn2en", "en2cn"]
    step: Literal[1, 2] = 1
    seq: int | None = None
    asr_text: str | None = None
    answer_text: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    first_voice_ms: int | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    used_keyboard: bool = False
    hint_level: int = Field(default=0, ge=0, le=3)
    used_hint_count: int = Field(default=0, ge=0)
    asr_confidence: float | None = None
    provider: str | None = None

    # ⚠️ 契约未定义、为实现 F15「限时到点行为」而新增（已上报为偏离项）。
    #    客户端检测到单题限时到点后置 true；服务端也会按 duration_ms 兜底判定。
    timed_out: bool = False

    @field_validator("asr_text", "answer_text")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SessionFinishIn(BaseModel):
    """`POST /api/sessions/{id}/finish`（接口 §9.3）。"""

    model_config = ConfigDict(extra="ignore")

    reason: Literal["completed", "manual", "timeout", "abandoned"] = "completed"
