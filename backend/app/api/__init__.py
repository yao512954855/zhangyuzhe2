"""API 路由包。

各工作包在本目录新增**自己的** `xxx.py`（文件名见《00-开发总纲》§5.2 归属矩阵）：

    from fastapi import APIRouter
    from app.core.response import ok

    router = APIRouter(prefix="/api/xxx", tags=["xxx"])

    @router.get("")
    async def list_xxx():
        return ok([])

注册由 `app/core/router_loader.py` **自动发现**完成，
**不需要（也不允许）修改任何注册文件** —— 这是并发开发不打架的前提。
"""
