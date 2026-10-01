"""管理端静态托管 —— 部署契约 §11 / ops/README.md §2.3。

生产环境下管理端（admin-web 构建产物）由**后端**在 `/admin` 前缀下托管，
浏览器入口 `http://{host}:8810/admin`。构建产物放在 `backend/static/admin/`
（ops 手册 §2.3：`把 dist\\ 整个拷到 backend\\static\\admin\\`）。

为什么做成 api 模块而不是在 main.py 里 `app.mount`：
- 本项目约定「新增后端接口只放 `app/api/xxx.py` 定义 `router`，不改 main.py」
  （自动发现见 `app/core/router_loader.py`）。
- `StaticFiles.mount` 必须动 main.py 且**挂在 404 处理之前**，侵入性大；
  用 router + `FileResponse` 完全等价，还便于做 SPA fallback 与目录穿越防护。

行为约定：
- `GET /admin`、`GET /admin/` → `index.html`；
- `GET /admin/{path}` → 静态目录内的真实文件；找不到（且是前端路由深链）时
  **fallback 回 `index.html`**（vue-router history 模式必须这样，否则刷新即 404）；
- 目录穿越（`..` / 绝对路径 / 软链逃逸）一律视为「文件不存在」→ fallback，
  绝不读静态目录之外的任何文件。

产物缺失时（`static/admin/` 没拷构建结果）返回 HTTP 404 + `10004`，
message 里给出可操作的修复指引（ops 手册口径），不静默白屏。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core.errors import BizError, ErrorCode

router = APIRouter(tags=["system"])

# backend/static/admin（本文件在 backend/app/api/ → parents[2] 即 backend/）
_STATIC_ROOT = Path(__file__).resolve().parents[2] / "static" / "admin"
_INDEX_NAME = "index.html"

_MISSING_MSG = (
    "管理端页面未部署：backend/static/admin/ 下没有构建产物。"
    "请在 admin-web 执行 npm run build，把 dist/ 整个拷到 backend/static/admin/"
)


def _safe_file(rel: str) -> Path | None:
    """把 URL 相对路径映射到静态目录内的真实文件；不存在 / 越界返回 None。"""
    if not rel or rel in (".", "/"):
        return None
    try:
        target = (_STATIC_ROOT / rel).resolve()
        root = _STATIC_ROOT.resolve()
    except OSError:  # noqa: BLE001 - Windows 上非法字符等极端输入
        return None
    # `is_relative_to` 同时挡住 `..` 与符号链接逃逸（resolve 后再比，不可绕过）
    if not target.is_relative_to(root):
        return None
    return target if target.is_file() else None


def _index_or_404() -> Path:
    index = _safe_file(_INDEX_NAME)
    if index is None:
        raise BizError(ErrorCode.NOT_FOUND, _MISSING_MSG, http_status=404)
    return index


@router.get("/admin")
@router.get("/admin/")
async def admin_index() -> FileResponse:
    """管理端入口页。"""
    return FileResponse(_index_or_404())


@router.get("/admin/{path:path}")
async def admin_static(path: str) -> FileResponse:
    """静态资源（js/css/图片等）；找不到则 SPA fallback 到 index.html。"""
    found = _safe_file(path)
    if found is not None:
        return FileResponse(found)
    # 真实文件不存在 → 前端深链刷新场景，交回 SPA 入口；
    # 若连产物都没部署，统一走 _index_or_404 的明确报错。
    return FileResponse(_index_or_404())
