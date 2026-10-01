"""学生管理接口 —— WP-BE-02 F6 ~ F12、F15（接口设计 §4.3 ~ §4.8）。

| 接口 | 说明 |
|---|---|
| `GET   /api/students` | 分页列表；`class_id=null` 查未分班；附 `practice_count` / `accuracy` |
| `GET   /api/students/import/template` | 下载名单导入模板（带班级下拉） |
| `POST  /api/students/import` | 两步式第一步：解析校验，**不落库** |
| `POST  /api/students/import/commit` | 两步式第二步：凭 `preview_token` 入库 |
| `GET   /api/import/report/{token}` | 导入错误明细（xlsx），路径取自接口设计 §4.4 的响应示例 |
| `POST  /api/students/{id}/assign-class` | 单个归班 |
| `POST  /api/students/batch-assign-class` | 批量归班 |
| `POST  /api/students/{id}/reset-password` | 重置密码（写审计） |
| `PATCH /api/students/{id}` | 改姓名 / 状态 / 班级（写审计） |

⚠️ **本模块持有 `/api/import/report/{token}`** —— 该路径出现在《接口设计》§4.4 的
学生导入响应示例里，且其实现（token → preview_store → `invalid_rows` → xlsx）
与业务类型无关。**BE-03 的内容导入应当直接复用本接口，不要重复注册同一路径**。

> 本轮（BE-03）已把该 handler 改造为**服务三种导入**：Starlette 按注册顺序匹配路由，
> 重复注册会让其中一条永久不可达，所以只能是一个 handler + 按 `kind` 分派。
> 原先它调 `UserService.load_import_preview()`，而后者会校验
> `kind == students_import`，会把 BE-03 的 token 误判成「已过期」。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile

from app.core.deps import CurrentUser, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.excel import build_template, xlsx_response_headers
from app.core.pagination import parse_pagination
from app.core.preview_store import get as preview_get
from app.core.response import ok, page
from app.schemas.student import (
    AssignClassIn,
    BatchAssignClassIn,
    ImportCommitIn,
    ResetPasswordIn,
    StudentOut,
    StudentPatchIn,
)
from app.services.class_service import ClassService
from app.services.user_service import UserService

router = APIRouter(tags=["students"])

# 契约 §5.4：文件上传 10 MB
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _parse_class_filter(raw: str | None) -> tuple[int | None, bool]:
    """解析 `class_id` 查询参数。

    Returns:
        `(class_id, unassigned_only)`：
        - 参数缺失 → `(None, False)` 不筛选
        - `class_id=null`（或 `none` / `unassigned`）→ `(None, True)` 只查未分班
        - `class_id=3` → `(3, False)`
    """
    if raw is None or raw.strip() == "":
        return None, False
    text = raw.strip().lower()
    if text in ("null", "none", "unassigned"):
        return None, True
    try:
        return int(text), False
    except ValueError as exc:
        raise BizError(ErrorCode.PARAM_INVALID, "class_id 必须是整数或 null") from exc


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


@router.get("/api/students")
async def list_students(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """学生列表。`accuracy` 走契约 §8.1 统一口径（由服务端给，前端不重算）。"""
    page_no, page_size = parse_pagination(request)
    qp = request.query_params
    class_id, unassigned_only = _parse_class_filter(qp.get("class_id"))

    def _opt(key: str) -> str | None:
        v = qp.get(key)
        return v.strip() or None if v else None

    items, total, unassigned = UserService.list_students(
        class_id=class_id,
        unassigned_only=unassigned_only,
        status=_opt("status"),
        register_source=_opt("register_source"),
        keyword=_opt("keyword"),
        page=page_no,
        page_size=page_size,
    )

    resp = page(
        [StudentOut.model_validate(i).model_dump() for i in items],
        total,
        page_no,
        page_size,
    )
    resp["data"]["unassigned_count"] = unassigned
    return resp


# ---------------------------------------------------------------------------
# 导入：模板 / 预览 / 入库 / 错误报告
# ---------------------------------------------------------------------------


@router.get("/api/students/import/template")
async def students_import_template(admin: CurrentUser = Depends(require_admin)) -> Response:
    """下载学生名单导入模板。

    ⚠️ 该接口在《接口设计》里**没有定义**（§6.1 的 `GET /api/import/template/{type}`
    只列了 `words` / `texts`）。但名单导入闭环需要它，故按 `/api/students/import/*`
    的命名补充实现，**已列入待姚工确认的契约补充项**。

    模板列：`学号`（必填）、`姓名`（必填）、`班级`（可选，填班级名称）、
    `初始密码`（可选，留空则用提交时的默认密码）。另可选加 `年级` / `学期` 两列，
    用于在班级重名时精确匹配。
    """
    headers = ["学号", "姓名", "班级", "初始密码"]
    validations: dict[str, list[str]] = {}
    names = [c["name"] for c in ClassService.active_index()]
    # Excel 的「序列」有效性上限 255 字符，超了就不加下拉，避免生成出打不开的文件
    if names and len(",".join(names)) <= 250:
        validations["班级"] = names

    content = build_template(
        headers,
        validations=validations or None,
        sheet_name="学生名单",
        example_notes=[
            "· 必填：学号、姓名；班级、初始密码可留空",
            "· 班级：填已建的班级名称（点单元格有下拉）；留空则导入后未分班",
            "· 初始密码：留空则用提交时设置的默认密码",
        ],
        example_rows=[
            ["2026001", "张小明", "三年级(1)班", ""],
            ["2026002", "李小红", "三年级(1)班", ""],
        ],
        column_widths={"学号": 18, "姓名": 12, "班级": 18, "初始密码": 14},
    )
    return Response(content=content, headers=xlsx_response_headers("学生名单导入模板.xlsx"))


@router.post("/api/students/import")
async def import_students(
    request: Request,
    file: UploadFile = File(..., description="学生名单 xlsx"),
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """名单导入第一步：解析 + 校验，返回 `preview_token` 与错误明细，**不落库**。"""
    declared = getattr(file, "size", None)
    if declared is not None and declared > MAX_UPLOAD_BYTES:
        raise BizError(
            ErrorCode.FILE_TOO_LARGE,
            f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB，请拆分后分批上传",
            http_status=413,
        )

    raw = await file.read()
    if not raw:
        raise BizError(ErrorCode.FILE_FORMAT_INVALID, "上传的文件是空的，请检查后重试")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise BizError(
            ErrorCode.FILE_TOO_LARGE,
            f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB，请拆分后分批上传",
            http_status=413,
        )

    data = UserService.import_preview(raw, operator_id=admin.id, ip=_client_ip(request))
    return ok(data)


@router.post("/api/students/import/commit")
async def import_students_commit(
    payload: ImportCommitIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """名单导入第二步：凭 token 一次性入库。重复学号为**更新**，且不重置其密码。"""
    data = UserService.import_commit(
        preview_token=payload.preview_token,
        default_password=payload.default_password,
        operator_id=admin.id,
        ip=_client_ip(request),
    )
    return ok(data)


@router.get("/api/import/report/{token}")
async def import_error_report(
    token: str,
    admin: CurrentUser = Depends(require_admin),
) -> Response:
    """下载导入错误明细（xlsx）。token 过期返回 `40003` + 明确提示。

    **一个 handler 服务三种导入**（名单 / 词条 / 课文），按 payload 的 `kind` 分派：
    名单走 `UserService.build_error_report`（固定表头），
    内容导入走 `ImportService.build_error_report`（表头由 preview 时写入的
    `report_headers` 决定）—— 词条 14 列、课文 8 列，渲染层不该去猜。

    不带 `kind` 的旧 payload 按名单处理（向后兼容）。
    """
    payload = preview_get(token)
    if not payload:
        raise BizError(
            ErrorCode.IMPORT_VALIDATION_FAILED,
            "导入预览已过期（有效期 30 分钟），请重新上传文件",
            data={"reason": "preview_token_expired"},
        )

    kind = payload.get("kind")
    if kind and kind != "students_import":
        # 惰性导入：BE-02 不应在模块级静态依赖 BE-03
        from app.services.import_service import ImportService

        content = ImportService.build_error_report(payload)
    else:
        content = UserService.build_error_report(payload)

    return Response(content=content, headers=xlsx_response_headers("导入错误明细.xlsx"))


# ---------------------------------------------------------------------------
# 归班 / 重置密码 / 修改
# ---------------------------------------------------------------------------


@router.post("/api/students/batch-assign-class")
async def batch_assign_class(
    payload: BatchAssignClassIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """批量归班。**部分失败不回滚整批**，明细逐条给出原因。"""
    data = UserService.batch_assign_class(
        student_ids=payload.student_ids,
        class_id=payload.class_id,
        operator_id=admin.id,
        ip=_client_ip(request),
    )
    return ok(data)


@router.post("/api/students/{student_id}/assign-class")
async def assign_class(
    student_id: int,
    payload: AssignClassIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """单个归班。`class_id=null` 表示移出班级。"""
    data = UserService.assign_class(
        student_id=student_id,
        class_id=payload.class_id,
        operator_id=admin.id,
        ip=_client_ip(request),
    )
    return ok(StudentOut.model_validate(_with_stats(data)).model_dump())


@router.post("/api/students/{student_id}/reset-password")
async def reset_password(
    student_id: int,
    payload: ResetPasswordIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """重置学生密码（写审计日志，10a F11）。"""
    UserService.reset_password(
        student_id=student_id,
        new_password=payload.new_password,
        operator_id=admin.id,
        ip=_client_ip(request),
    )
    return ok({"student_id": student_id, "reset": True})


@router.patch("/api/students/{student_id}")
async def patch_student(
    student_id: int,
    payload: StudentPatchIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """改姓名 / 状态 / 班级。

    `class_id` 的三态（不传 / 传 null / 传值）由 `model_fields_set` 判定 ——
    Pydantic 无法用默认值区分「不传」与「传 null」，见 `schemas/student.py` 的说明。
    """
    supplied = payload.model_fields_set
    fields: dict[str, Any] = {}

    if "real_name" in supplied:
        if payload.real_name is None:
            raise BizError(ErrorCode.PARAM_INVALID, "姓名不能为空")
        fields["real_name"] = payload.real_name
    if "status" in supplied:
        if payload.status is None:
            raise BizError(ErrorCode.PARAM_INVALID, "status 不能为空")
        fields["status"] = payload.status
    if "class_id" in supplied:
        # None 是合法值：表示把该生移出班级（进入未分班）
        fields["class_id"] = payload.class_id

    data = UserService.update_student(
        student_id=student_id,
        fields=fields,
        operator_id=admin.id,
        ip=_client_ip(request),
    )
    return ok(StudentOut.model_validate(_with_stats(data)).model_dump())


def _with_stats(user: dict[str, Any]) -> dict[str, Any]:
    """给单个学生补上 `practice_count` / `accuracy`（与列表接口同口径）。"""
    out = dict(user)
    out.update(UserService.student_stats(int(user["id"])))
    return out
