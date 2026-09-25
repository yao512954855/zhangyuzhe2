"""班级管理接口 —— WP-BE-02 F4 / F5 + 契约 §14.1 第 1 条（接口设计 §4.1 ~ §4.2）。

| 接口 | 说明 |
|---|---|
| `GET   /api/classes` | 分页列表，附 `student_count`；响应另带全局 `unassigned_count` |
| `GET   /api/classes/options` | **注册页用的班级选项（免鉴权）**，只有 `id`/`name`/`grade`/`semester` |
| `POST  /api/classes` | 创建；重名返回 `10001` 且提示写清是哪个班重名 |
| `PATCH /api/classes/{id}` | 改名称 / 备注 / 归档（契约 §14.1：一期用本接口实现归档停用） |
| `GET   /api/classes/{id}/students/credentials` | 导出「学号-初始密码」对照表（xlsx） |

⚠️ **`unassigned_count` 的落点说明**：《接口设计》§4.1 的示例把它画在每个班级项里面，
但「某个班有多少人未分班」在语义上不成立（未分班的人恰恰不属于任何班）。
10a F4 只要求「返回 `student_count`、`unassigned_count`」，故本实现把
`student_count` 放列表项、把 `unassigned_count`（= 全校未分班人数）放在 `data` 顶层，
与 `GET /api/students` 的写法保持一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.core.deps import CurrentUser, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.excel import xlsx_response_headers
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.classes import ClassCreateIn, ClassOut, ClassPatchIn
from app.services.class_service import ClassService
from app.services.user_service import UserService

router = APIRouter(prefix="/api/classes", tags=["classes"])

#: `GET /api/classes/options` 一次给全（不分页）。一个学校的班级量级是几十，
#: 500 已是极宽松上限；超过说明数据异常，宁可少给也不让响应无限膨胀。
MAX_CLASS_OPTIONS = 500


def _opt_int(raw: str | None, field: str) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise BizError(ErrorCode.PARAM_INVALID, f"{field} 必须是整数") from exc


def _opt_str(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = raw.strip()
    return s or None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("")
async def list_classes(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """班级列表。默认只返回活跃班级，`status=archived` 看归档，`status=all` 看全部。"""
    page_no, page_size = parse_pagination(request)
    qp = request.query_params

    items, total = ClassService.list_classes(
        grade=_opt_int(qp.get("grade"), "grade"),
        semester=_opt_str(qp.get("semester")),
        status=_opt_str(qp.get("status")),
        keyword=_opt_str(qp.get("keyword")),
        page=page_no,
        page_size=page_size,
    )

    resp = page(
        [ClassOut.model_validate(i).model_dump() for i in items],
        total,
        page_no,
        page_size,
    )
    resp["data"]["unassigned_count"] = ClassService.unassigned_count()
    return resp


@router.get("/options")
async def class_options(request: Request) -> dict:
    """注册页的班级选项清单（**免鉴权**）。

    为什么单独开一条免鉴权接口、而不是复用上一条 `GET /api/classes`：

    1. **注册发生在登录之前** —— 学生打开注册页时没有任何 token，带鉴权的接口
       永远返回 `20002`，UI 规范 §7.3「班级选择用卡片列表」就只能永远显示「先不选」。
    2. **字段必须收窄** —— 上一条是管理端接口，响应含 `student_count` /
       `unassigned_count` 等**管理信息**，不能给未登录者看。

    与 `GET /api/ping`（§3.1）同级的公开信息：只暴露**校内公开的班级名**，
    不含任何个人信息，也不含人数。

    ⚠️ **放在 `/{class_id}` 路由之前注册**：Starlette 按注册顺序匹配，
    若将来新增 `GET /api/classes/{class_id}`，后注册的路径可能把 `options`
    当成 `class_id` 吞掉。
    """
    qp = request.query_params
    items, total = ClassService.list_classes(
        grade=_opt_int(qp.get("grade"), "grade"),
        semester=_opt_str(qp.get("semester")),
        status=None,  # 只看活跃班级：已归档的班不再接收新学生
        page=1,
        page_size=MAX_CLASS_OPTIONS,
    )
    return ok(
        {
            "items": [
                {
                    "id": int(i["id"]),
                    "name": i["name"],
                    "grade": int(i["grade"]),
                    "semester": i["semester"],
                }
                for i in items
            ],
            "total": total,
        }
    )


@router.post("")
async def create_class(
    payload: ClassCreateIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """创建班级。"""
    created = ClassService.create(
        name=payload.name,
        grade=payload.grade,
        semester=payload.semester,
        school_year=payload.school_year,
        remark=payload.remark,
        operator_id=admin.id,
        ip=_client_ip(request),
    )
    return ok(ClassOut.model_validate(created).model_dump())


@router.patch("/{class_id}")
async def update_class(
    class_id: int,
    payload: ClassPatchIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """改名称 / 备注 / 归档。"""
    updated = ClassService.update(
        class_id=class_id,
        name=payload.name,
        remark=payload.remark,
        status=payload.status,
        operator_id=admin.id,
        ip=_client_ip(request),
    )
    return ok(ClassOut.model_validate(updated).model_dump())


@router.get("/{class_id}/students/credentials")
async def export_credentials(
    class_id: int,
    request: Request,
    token: str | None = None,
    admin: CurrentUser = Depends(require_admin),
) -> Response:
    """导出学生账号对照表（xlsx）。

    两种用法：

    1. **带 `token`** —— 导出刚刚那次导入的「学号-姓名-班级-初始密码」。
       密码是 bcrypt 哈希，事后无法反推，所以对照表只能来自导入那一刻的快照
       （有效期 30 分钟，见 `UserService.import_commit`）。
    2. **不带 `token`** —— 导出该班学生名单（学号 / 姓名 / 班级），**没有密码列**。
       `class_id=0` 表示不限班级（全校学生）。

    需要重发密码时请用 `POST /api/students/{id}/reset-password`。
    """
    from app.core.excel import write_xlsx

    if token:
        items = UserService.credentials_items(token)
        headers = ["学号", "姓名", "班级", "初始密码", "备注"]
        rows = [
            [
                i.get("username"),
                i.get("real_name"),
                i.get("class_name") or _class_name(i.get("class_id")),
                i.get("password") or "",
                i.get("note") or "",
            ]
            for i in items
        ]
        filename = "学生账号初始密码对照表.xlsx"
    else:
        items = UserService.class_students_brief(None if class_id == 0 else class_id)
        headers = ["学号", "姓名", "班级"]
        rows = [[i["username"], i["real_name"], i.get("class_name") or ""] for i in items]
        filename = "学生名单.xlsx"

    content = write_xlsx(headers, rows, sheet_name="学生账号")
    return Response(content=content, headers=xlsx_response_headers(filename))


def _class_name(class_id: Any) -> str:
    if class_id is None:
        return "未分班"
    return ClassService.name_map([int(class_id)]).get(int(class_id), "未分班")
