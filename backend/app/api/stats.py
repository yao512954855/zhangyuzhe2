"""统计看板接口 —— WP-BE-07 F1 / F2 / F3 / F8 / F10 / F11（10b）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `GET /api/stats/class/{id}` | admin | 班级概览（F1） |
| `GET /api/stats/unit/{id}` | admin | 单元掌握度（F2） |
| `GET /api/stats/student/{id}` | admin | 学生个人报告（F3） |
| `GET /api/stats/asr` | admin | ASR 用量与失败率（F8） |
| `GET /api/audit-logs` | admin | 审计日志查询（F10） |
| `GET /api/stats/export` | admin | 导出 xlsx（F11） |

**全部只读**，不写任何业务表。申诉复核的写入在 `app/api/appeals.py`。

### 为什么查询参数手工解析而不建 Schema

`date_from` / `date_to` 的格式错误必须返回**带字段名的中文提示**
（`10001` + 「date_from 格式应为 YYYY-MM-DD」），管理端要能把错误标到具体输入框。
Pydantic 默认吐的是英文 message，还得再做一层映射；而分页又已经由
`core.pagination` 统一处理，再建一遍模型会让「上限 100」出现两处实现。
所以这里统一走 `Request.query_params` + `stats_service` 里的小工具函数。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.core.deps import CurrentUser, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.excel import write_xlsx, xlsx_response_headers
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.services.asr_stats_service import AsrStatsService
from app.services.stats_service import StatsService

router = APIRouter(tags=["stats"])


def _int_param(
    request: Request, name: str, default: int, *, minimum: int, maximum: int
) -> int:
    """读一个整数查询参数并夹到区间内（**超范围报错，不静默夹值**）。

    静默夹值（比如 `top_n=999` 悄悄变成 50）会让管理端以为「Top 50 就是全部」，
    是比报错更难发现的问题。所以越界一律 `10001`。
    """
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BizError(ErrorCode.PARAM_INVALID, f"{name} 必须是整数") from exc
    if value < minimum or value > maximum:
        raise BizError(
            ErrorCode.PARAM_INVALID, f"{name} 必须在 {minimum} 到 {maximum} 之间"
        )
    return value


def _optional_str(request: Request, name: str) -> str | None:
    raw = request.query_params.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


# --------------------------------------------------------------------------- F1


@router.get("/api/stats/class/{class_id}")
async def class_stats(
    class_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """F1 班级概览：人数 / 今日活跃 / 完成率 / 平均正确率 / 平均用时 / 趋势 / 错题 Top N。

    `date_from` / `date_to` 默认近 7 天（含今天）。`top_n` 默认 10，上限 50。
    """
    return ok(
        StatsService.class_overview(
            class_id,
            date_from=_optional_str(request, "date_from"),
            date_to=_optional_str(request, "date_to"),
            top_n=_int_param(request, "top_n", 10, minimum=1, maximum=50),
        )
    )


# --------------------------------------------------------------------------- F2


@router.get("/api/stats/unit/{unit_id}")
async def unit_stats(
    unit_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """F2 单元掌握度：词条数 / 学生数 / 正确率 / 已掌握比例 / 未完成学生 / 难词榜。"""
    return ok(
        StatsService.unit_overview(
            unit_id,
            hard_word_limit=_int_param(request, "hard_word_limit", 20, minimum=1, maximum=100),
        )
    )


# --------------------------------------------------------------------------- F3


@router.get("/api/stats/student/{student_id}")
async def student_stats(
    student_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """F3 学生个人报告：练习天数 / 场次 / 正确率 / 已掌握 / 连续天数 / 最近场次 / 背诵 / 错词。"""
    return ok(
        StatsService.student_report(
            student_id,
            recent_limit=_int_param(request, "recent_limit", 10, minimum=1, maximum=50),
        )
    )


# --------------------------------------------------------------------------- F8


@router.get("/api/stats/asr")
async def asr_stats(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """F8 ASR 用量：按天/厂商的调用次数、音频总时长、平均延迟、失败率。

    数据来自 `asr_daily_stats`（已被日聚合的老数据）+ `asr_logs`（最近 90 天明细）。
    """
    return ok(
        AsrStatsService.usage(
            date_from=_optional_str(request, "date_from"),
            date_to=_optional_str(request, "date_to"),
            provider=_optional_str(request, "provider"),
        )
    )


# -------------------------------------------------------------------------- F10


@router.get("/api/audit-logs")
async def audit_logs(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """F10 审计日志：分页 + 按用户/动作/时间筛。

    `action` 走**前缀匹配**（`?action=student.` 能筛出全部学生相关动作），
    因为动作名是 `域.动词` 结构，按域筛是最常用的用法。
    """
    pg, ps = parse_pagination(request)
    user_id = _optional_str(request, "user_id")
    parsed_user: int | None = None
    if user_id is not None:
        try:
            parsed_user = int(user_id)
        except ValueError as exc:
            raise BizError(ErrorCode.PARAM_INVALID, "user_id 必须是整数") from exc

    items, total = StatsService.audit_logs(
        page=pg,
        page_size=ps,
        user_id=parsed_user,
        action=_optional_str(request, "action"),
        date_from=_optional_str(request, "date_from"),
        date_to=_optional_str(request, "date_to"),
    )
    return page(items, total, pg, ps)


# -------------------------------------------------------------------------- F11


@router.get("/api/stats/export")
async def export_stats(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> Response:
    """F11 导出成绩单 xlsx。

    `?type=class&id=3` → 班级成绩单（每行一名学生）
    `?type=student&id=42` → 学生成绩单（每行一场练习）

    ⚠️ 这是**文件下载**，返回的是 `Response` 而不是 `ok()` 包装的 JSON ——
    统一响应结构只适用于 JSON 接口；把 xlsx 二进制塞进 `data` 字段会让管理端
    既要解析 base64 又要拼 Blob。项目内既有的下载接口
    （`/api/import/{type}/template`）也是这个写法。
    """
    kind = _optional_str(request, "type")
    if kind not in ("class", "student"):
        raise BizError(ErrorCode.PARAM_INVALID, "type 必须是 class 或 student")
    target = _int_param(request, "id", 0, minimum=0, maximum=2**31 - 1)
    if target <= 0:
        raise BizError(ErrorCode.PARAM_INVALID, "id 必须是正整数")

    if kind == "class":
        filename, rows = StatsService.export_class_rows(target)
        headers = StatsService.CLASS_EXPORT_HEADERS
        sheet = "班级成绩"
    else:
        filename, rows = StatsService.export_student_rows(target)
        headers = StatsService.STUDENT_EXPORT_HEADERS
        sheet = "学生成绩"

    content = write_xlsx(headers, rows, sheet_name=sheet)
    return Response(content=content, headers=xlsx_response_headers(filename))
