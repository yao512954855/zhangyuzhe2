"""内容导入接口 —— WP-BE-03 F11 ~ F15（接口设计 §6.1 ~ §6.5）。

| 接口 | 说明 |
|---|---|
| `GET  /api/import/template/{type}` | 下载模板（`words` / `texts`），带下拉有效性 |
| `POST /api/import/words/preview` | 词条解析校验，返回 `preview_token` + `invalid_rows`，**不落库** |
| `POST /api/import/words/commit` | 凭 token 入库，单事务；`create_missing_units` / `on_duplicate` |
| `POST /api/import/texts/preview` | 课文解析 + 段落切分 + 拼音等长校验 |
| `POST /api/import/texts/commit` | 接受前端回传调整后的 `recite_scope` 入库 |

### 关于 `/api/import/report/{token}`

10a F16 把错误报告划给 BE-03，但**该路径已由 WP-BE-02 的 `app/api/students.py` 注册**
（它源自《接口设计》§4.4 的响应示例，学生名单导入先用到）。
Starlette 路由按注册顺序匹配，重复注册会让其中一个永久不可达，
因此本模块**不注册**它 —— 两个包共用同一个 handler，
报告表头由 preview 时写进 payload 的 `report_headers` 决定。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile

from app.core.deps import CurrentUser, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.excel import xlsx_response_headers
from app.core.response import ok
from app.schemas.imports import TextsImportCommitIn, WordsImportCommitIn
from app.services.import_service import ImportService

router = APIRouter(tags=["imports"])

#: 契约 §5.4：文件上传 10 MB
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _read_upload(file: UploadFile) -> bytes:
    """读上传文件并做大小 / 空文件校验。

    **两道大小检查**：先看 `UploadFile.size`（Starlette 已解析出的大小，能省掉读盘），
    读完再看实际字节数 —— 前者在部分客户端（分块上传、未带 Content-Length）是 `None`。
    """
    declared = getattr(file, "size", None)
    if declared is not None and declared > MAX_UPLOAD_BYTES:
        raise BizError(
            ErrorCode.FILE_TOO_LARGE,
            f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB，请拆分成多个文件后分批上传",
            http_status=413,
        )

    raw = await file.read()
    if not raw:
        raise BizError(ErrorCode.FILE_FORMAT_INVALID, "上传的文件是空的，请检查后重试")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise BizError(
            ErrorCode.FILE_TOO_LARGE,
            f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB，请拆分成多个文件后分批上传",
            http_status=413,
        )
    return raw


# ---------------------------------------------------------------------------
# 模板
# ---------------------------------------------------------------------------


@router.get("/api/import/template/{type}")
async def download_template(
    type: str,  # noqa: A002 - 路径参数名严格对齐《接口设计》§6.1 的 `{type}`
    admin: CurrentUser = Depends(require_admin),
) -> Response:
    """下载导入模板（10a F11 / 接口设计 §6.1）。

    `words` 14 列、`texts` 8 列，**均带数据有效性下拉**（U1）。
    下拉是给不熟 Excel 的管理者省事的，「背诵范围」列留空即表示全部段落。
    """
    if type == "words":
        content = ImportService.words_template()
        filename = "英语词条导入模板.xlsx"
    elif type == "texts":
        content = ImportService.texts_template()
        filename = "语文课文导入模板.xlsx"
    else:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            f"模板类型「{type}」不存在，只能是 words（英语词条）或 texts（语文课文）",
        )

    return Response(content=content, headers=xlsx_response_headers(filename))


# ---------------------------------------------------------------------------
# 英语词条
# ---------------------------------------------------------------------------


@router.post("/api/import/words/preview")
async def words_preview(
    file: UploadFile = File(..., description="英语词条 xlsx"),
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """词条导入第一步：解析 + 校验（10a F12）。

    返回 `summary`（`total` / `valid` / `invalid` / `duplicated`）、
    `new_units`（未建单元，供前端弹确认）、`invalid_rows`（行号 + 原因）。
    **不落库**，token 有效期 30 分钟。
    """
    raw = await _read_upload(file)
    data = ImportService.words_preview(raw, file_name=file.filename)
    return ok(data)


@router.post("/api/import/words/commit")
async def words_commit(
    payload: WordsImportCommitIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """词条导入第二步：入库（10a F13）。

    - `create_missing_units=false` 且文件含新单元 → 明确报错，**不建单元不落词条**（U3）
    - `on_duplicate=skip` → `skipped` 计入全部重复行（U4）
    - `on_duplicate=overwrite` → 更新内容字段，**`status` 不覆盖**（U5）
    """
    data = ImportService.words_commit(
        preview_token=payload.preview_token,
        create_missing_units=payload.create_missing_units,
        on_duplicate=payload.on_duplicate,
        operator_id=admin.id,
        ip=_ip(request),
    )
    return ok(data)


# ---------------------------------------------------------------------------
# 语文课文
# ---------------------------------------------------------------------------


@router.post("/api/import/texts/preview")
async def texts_preview(
    file: UploadFile = File(..., description="语文课文 xlsx"),
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """课文导入第一步：解析 + 段落切分 + 拼音等长校验（10a F14）。

    返回每篇课文的 `paragraphs`（供前端渲染**背诵范围勾选框**）、
    `pinyin_check`、`total_chars`，以及 `pinyin_issues`（无法拼音化的字与位置）。

    ⚠️ `pinyin_check='mismatch'` 的行**不能入库**，但照常返回 paragraphs ——
    管理者要看到「第几个字出的问题」才知道回 Excel 改哪里（U7）。
    """
    raw = await _read_upload(file)
    data = ImportService.texts_preview(raw, file_name=file.filename)
    return ok(data)


@router.post("/api/import/texts/commit")
async def texts_commit(
    payload: TextsImportCommitIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """课文导入第二步：入库（10a F15），接受前端调整后的 `recite_scope`。

    按 **Excel 行号**映射回传的范围（不是数组下标 —— 非法行被剔除后下标会错位），
    入库时 `total_chars` / `content_plain` / `content_pinyin` 按新范围**全部重算**（U8）。
    """
    scopes = {int(item.row): list(item.recite_scope) for item in payload.texts}
    data = ImportService.texts_commit(
        preview_token=payload.preview_token,
        scopes=scopes,
        operator_id=admin.id,
        ip=_ip(request),
    )
    return ok(data)
