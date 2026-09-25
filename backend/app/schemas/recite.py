"""背诵流程的请求模型 —— WP-BE-05 / 接口设计 §9.4。

响应统一用 `ok()` 包装 dict（字段少、要与契约示例逐字对齐），
这里只放**需要校验的请求体**。

### `matched_chars` 为什么还要收

F5 要求服务端重算、不采信客户端。那为什么还在模型里留着 `matched_chars`？
——因为 App 可能已经按接口示例发出这个字段，模型**必须接得住**，
否则会因「多余字段」被拒（`extra="forbid"`）。
收到后**直接丢弃**，服务端只认自己的对齐结果。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FinishReason = Literal["completed", "manual", "timeout"]
TriggerBy = Literal["manual", "auto"]
EventType = Literal["matched", "missed", "hint", "restart", "noise"]


class RecitationCreateIn(BaseModel):
    """`POST /api/recitations`（§9.4）。"""

    model_config = ConfigDict(extra="ignore")

    session_id: int = Field(ge=1)
    text_id: int = Field(ge=1)
    scope: list[int] = Field(
        default_factory=list,
        description="背诵段落下标数组；空数组 = 全文背诵（与 §8.7 的 recite_scope 同语义）",
    )

    @field_validator("scope")
    @classmethod
    def _clean_scope(cls, value: list[int]) -> list[int]:
        cleaned: list[int] = []
        for item in value or []:
            try:
                index = int(item)
            except (TypeError, ValueError):
                raise ValueError("scope 必须是非负整数数组") from None
            if index < 0:
                raise ValueError("scope 不能包含负数")
            if index not in cleaned:
                cleaned.append(index)
        return cleaned


class FeedIn(BaseModel):
    """`POST /api/recitations/{id}/feed`（**契约未定义，实现补充**）。

    App 走 WebSocket 时会直接进引擎；这个 HTTP 通道是给
    「断线后补喂」与「接口级验收」用的 —— WebSocket 不方便写自动化测试。
    """

    model_config = ConfigDict(extra="ignore")

    partial_text: str = Field(default="", description="ASR 的**累计**文本，不是增量")
    silence_ms: int = Field(default=0, ge=0, description="自上次发声以来的静音时长")


class HintIn(BaseModel):
    """`POST /api/recitations/{id}/hint`（**契约未定义，实现补充**）。

    - `level` 显式给出 → 学生主动点的「我卡住了」；
    - `level` 为 `null` → 服务端按静音 / 连续漏字自动判定。
    """

    model_config = ConfigDict(extra="ignore")

    level: int | None = Field(default=None, ge=1, le=3)
    trigger_by: TriggerBy = "auto"
    silence_ms: int = Field(default=0, ge=0)


class RecitationEventIn(BaseModel):
    """单条补报事件（F2）。"""

    model_config = ConfigDict(extra="ignore")

    pos: int = Field(default=0, ge=0)
    event_type: EventType
    char: str | None = None
    hint_level: int | None = Field(default=None, ge=1, le=3)
    trigger_by: TriggerBy | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)


class EventsReportIn(BaseModel):
    """`POST /api/recitations/{id}/events`（F2，App 断线补报）。"""

    model_config = ConfigDict(extra="ignore")

    events: list[RecitationEventIn] = Field(default_factory=list)


class SettleIn(BaseModel):
    """`POST /api/recitations/{id}/settle`（§9.4）。

    `matched_chars` / `missed_chars` / `hint_count` 三个字段**只收不用**（F5 防刷分）。
    """

    model_config = ConfigDict(extra="ignore")

    transcript: str = Field(default="", description="本次背诵的完整识别文本（累计）")
    matched_chars: int | None = Field(default=None, description="仅参考，服务端重算")
    missed_chars: list[int] | None = Field(default=None, description="仅参考，服务端重算")
    hint_count: dict[str, Any] | None = Field(default=None, description="仅参考，服务端重算")
    restart_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    first_voice_ms: int | None = Field(default=None, ge=0)
    finish_reason: FinishReason = "manual"
