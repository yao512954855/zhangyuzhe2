"""应用入口 —— 10a《WP-BE-01 基础设施与契约骨架》。

职责：
1. 加载配置 → 初始化日志（失败即退出，不静默用默认值）
2. 创建 FastAPI 实例并装配中间件（trace_id / CORS / 限流 / 幂等）
3. 注册全局异常处理器（BizError / 参数校验 / 未捕获 / 404-405）
4. 启动初始化：执行迁移 → 首次创建 admin 账号
5. **自动路由发现**（新增 api 模块无需改本文件）
6. 内置健康检查 `GET /api/health`（免鉴权）
7. 启停 WP-BE-08 的后台任务（IP 变化检测 / UDP 发现应答 / 日任务）

中间件顺序（从外到内）：TraceId → CORS → RateLimit → Idempotency → 路由。
FastAPI 的 `add_middleware` 是**后加的在更外层**，所以下面按「内 → 外」倒序添加。

---

### ⚠️ 跨包改动记录（WP-BE-08 实施时对本文件动了三处，已上报待确认）

本文件归属 **WP-BE-01**。下述三处由 **WP-BE-08** 改动：

| # | 改动 | 依据 |
|---|---|---|
| 1 | **删除** `/api/ping` 桩 | 本文件的 BE-01 原 docstring 已写明「BE-08 实施时必须删掉这个桩」。自动路由在本文件**之后**注册，同名路由先注册者生效 —— 不删则 `api/discovery.py` 的 `/api/ping` 永远不生效（前序包记为缺陷 **#41**） |
| 2 | `lifespan` 启停后台任务 | 总纲给 BE-08 分了 `tasks/scheduler.py`，却**没有规定谁来启动它**。而本项目用的是自定义 `lifespan`，Starlette 在此情况下**不执行** `on_startup` / `@app.on_event("startup")`（见 `starlette.routing.Router.__init__`：只有 lifespan 为 `None` 时才用 `_DefaultLifespan`），所以 `app/api/*.py` **无法**自挂后台任务 —— 只能在这里加钩子 |
| 3 | `/api/health` 增加 `degraded` / `degraded_reasons` | 10b 验收用例 U9 明确要求「`/api/health` 可查到」降级告警 |
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.core.config import get_asr_config, get_settings
from app.core.db import read_conn, utc_now_iso
from app.core.errors import DEFAULT_MESSAGES, BizError, ErrorCode
from app.core.idempotency import IdempotencyMiddleware
from app.core.logging import get_logger, setup_logging
from app.core.migrate import MigrationError, get_current_version, run_migrations
from app.core.rate_limit import RateLimitMiddleware
from app.core.response import fail, ok
from app.core.router_loader import load_routers
from app.services.discovery import state as discovery_state
from app.tasks.scheduler import start_background_tasks, stop_background_tasks
from app.core.trace import (
    TRACE_ID_HEADER,
    TraceIdMiddleware,
    get_trace_id,
    trace_context_from_scope,
)

# ---- 启动即加载配置：缺失必填项直接退出（10a F2）----
# ⚠️ `settings` 是 **import 时的快照**，只供「这辈子只做一次」的 import 期初始化用
#   （日志级别、CORS 白名单、`python -m app.main` 的监听参数）。
#   **运行期逻辑一律现取 `get_settings()`** —— 否则测试换库 / 换配置时不生效，
#   且只在多模块混跑时才暴露（见 `_ensure_admin_account` 的说明）。
settings = get_settings()
setup_logging(settings.LOG_LEVEL, settings.log_dir)

logger = get_logger(__name__)


def bootstrap() -> None:
    """启动初始化：执行数据库迁移。迁移失败则拒绝启动。"""
    s = get_settings()
    try:
        applied = run_migrations()
    except MigrationError as exc:
        logger.error("数据库迁移失败，服务拒绝启动：%s", exc)
        raise

    if applied:
        logger.info("数据库迁移完成，应用版本：%s", applied)
    else:
        logger.info("数据库已是最新，无需迁移。")

    logger.info("数据库文件：%s", s.db_file)
    logger.info("服务监听：%s:%s", s.APP_HOST, s.APP_PORT)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bootstrap()
    # ---- WP-BE-08：启动后台任务（启动自检 / UDP 发现应答 / IP 变化检测 / 日任务）
    # ⚠️ 必须在这里显式启动：本应用传入的是**自定义 lifespan**，
    #   Starlette 在这种情况下**不会**执行 `on_startup` / `@app.on_event("startup")`
    #   注册的处理器（`Router.__init__` 只在 lifespan 为 None 时才用 `_DefaultLifespan`）。
    #   于是 `app/api/*.py` 里**没有任何办法**自己挂后台任务 —— 只能由本文件挂钩子。
    #   `start_background_tasks` 幂等，进程内只有这一个调度器。
    #   **先 await 再 yield**：HTTP 开始接客时 UDP 广播发现必须已经绑好，
    #   否则 App 启动瞬间发的广播会扑空（它只重试 3 轮共 6 秒）。
    await start_background_tasks()
    try:
        yield
    finally:
        # 收尾必须 await：要取消调度任务并**释放 UDP 9527 端口**，
        # 否则测试里下一次 `TestClient` 起来会绑不上（表现为广播发现静默失效）。
        await stop_background_tasks()
        logger.info("服务已停止。")


app = FastAPI(
    title="背记系统 后端 API",
    description="中小学英语单词 / 语文课文背诵系统（局域网部署）",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ---------------------------------------------------------------------------
# 中间件：add 顺序 = 内 → 外
# ---------------------------------------------------------------------------
app.add_middleware(IdempotencyMiddleware)

app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)

app.add_middleware(TraceIdMiddleware)


# ---------------------------------------------------------------------------
# 全局异常处理器（10a F7）
# ---------------------------------------------------------------------------
@app.exception_handler(BizError)
async def _handle_biz_error(request: Request, exc: BizError) -> JSONResponse:
    """业务异常 —— 按其 code 返回，HTTP 默认 200（契约 §5.1）。"""
    return JSONResponse(
        status_code=exc.http_status,
        content=fail(exc.code, exc.message, exc.data),
    )


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """参数校验失败 → `10001`，**message 必须带具体字段名**（验收用例 U7）。"""
    fields: list[str] = []
    detail: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
        field_name = ".".join(loc) or "参数"
        if field_name not in fields:
            fields.append(field_name)
        detail.append({"field": field_name, "message": err.get("msg", "")})

    message = (
        f"参数不正确：{('、'.join(fields))}" if fields else DEFAULT_MESSAGES[ErrorCode.PARAM_INVALID]
    )
    logger.info("参数校验失败：%s", detail)
    return JSONResponse(
        status_code=200,
        content=fail(ErrorCode.PARAM_INVALID, message, {"errors": detail}),
    )


@app.exception_handler(StarletteHTTPException)
async def _handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """兜住 404 / 405 等框架级异常，响应体保持统一结构。

    HTTP 状态码保留原值（契约 §5.1：404 路径不存在属 HTTP 非 200 场景）。
    """
    if exc.status_code == 404:
        code = ErrorCode.NOT_FOUND
    elif exc.status_code == 405:
        code = ErrorCode.PARAM_INVALID
    elif exc.status_code == 401:
        code = ErrorCode.UNAUTHORIZED
    elif exc.status_code == 403:
        code = ErrorCode.FORBIDDEN
    else:
        code = ErrorCode.INTERNAL_ERROR

    message = DEFAULT_MESSAGES.get(code, str(exc.detail))
    if exc.status_code >= 500:
        logger.error("HTTP %s 于 %s", exc.status_code, request.url.path)
    # 若 5xx 是在别的中间件里抛出的，本 handler 会在 TraceIdMiddleware 之外执行，
    # 需先从 scope 恢复 trace_id 上下文（详见 app/core/trace.py 模块说明）。
    with trace_context_from_scope(request.scope):
        return JSONResponse(
            status_code=exc.status_code,
            content=fail(code, message),
            headers={TRACE_ID_HEADER: get_trace_id()},
        )


@app.exception_handler(Exception)
async def _handle_uncaught(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常 → `10006`。

    日志记**完整堆栈**（带 trace_id，可按其检索，验收用例 U6）；
    响应体**只给 trace_id**，不泄露内部细节。

    ⚠️ 本 handler 由 **ServerErrorMiddleware**（最外层中间件）调用，位置在
    `TraceIdMiddleware` 之外 —— 异常穿出时 contextvars 已被 reset。因此必须
    先用 `trace_context_from_scope()` 从 `request.scope` 把 trace_id 恢复回来，
    否则响应体里的 `trace_id` 会是空串。
    """
    with trace_context_from_scope(request.scope):
        logger.error(
            "未捕获异常于 %s %s", request.method, request.url.path, exc_info=True
        )
        return JSONResponse(
            status_code=500,
            content=fail(
                ErrorCode.INTERNAL_ERROR, DEFAULT_MESSAGES[ErrorCode.INTERNAL_ERROR]
            ),
            headers={TRACE_ID_HEADER: get_trace_id()},
        )


# ---------------------------------------------------------------------------
# 健康检查（10a F19，**免鉴权**）
# ---------------------------------------------------------------------------
@app.get("/api/health", tags=["system"])
async def health() -> dict[str, Any]:
    """返回服务版本、DB 可达性、迁移版本号、ASR 配置是否就绪、降级告警。

    `degraded_reasons` 来自 WP-BE-08 的内存状态（**不查库**，见 10b F12 / U9）。
    它是「此刻有没有东西在降级」的实时视图，例如「UDP 发现端口被占」。
    """
    db_ok = True
    schema_version = 0
    try:
        with read_conn() as conn:
            conn.execute("SELECT 1").fetchone()
            schema_version = get_current_version(conn)
    except Exception as exc:  # noqa: BLE001
        logger.error("健康检查：数据库不可达 %s", exc)
        db_ok = False

    asr = get_asr_config()
    degraded = discovery_state.degraded_reasons()

    return ok(
        {
            "service": "bjsys-backend",
            "version": __version__,
            "db": db_ok,
            "schema_version": schema_version,
            "asr_ready": asr.ready,
            "asr_provider": asr.provider,
            "server_time": utc_now_iso(),
            "degraded": bool(degraded),
            "degraded_reasons": degraded,
        }
    )


# ---------------------------------------------------------------------------
# 注：`GET /api/ping` 由 **WP-BE-08** 提供（`app/api/discovery.py`）。
#
# BE-01 曾在此留了一个同名桩，并在 docstring 里写明「BE-08 实施时必须删掉它」——
# 因为**自动路由在本文件之后注册，同名路由先注册者生效**，桩不删掉的话
# `discovery.py` 里写的 `/api/ping` 永远不生效（前序包记为缺陷 #41）。
# WP-BE-08 已删除该桩，本文件不再持有此路由。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 自动路由发现（契约 §5.4）—— 必须放在所有 @app.get 之后
# ---------------------------------------------------------------------------
REGISTERED_ROUTERS: list[str] = load_routers(app)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=False,
    )
