"""自动路由发现 —— 契约《01-共享契约冻结》§5.4 / 10a F12。

**这是并发开发能成立的关键机制之一。**
每个工作包只管在自己独占的 `app/api/xxx.py` 里定义名为 `router` 的 `APIRouter`，
不需要修改任何注册文件 —— 因此不存在「多个包抢改同一个文件」的冲突。

用法（main.py）：
    from app.core.router_loader import load_routers
    load_routers(app)
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_PACKAGE = "app.api"


def load_routers(app: Any, package: str = DEFAULT_PACKAGE) -> list[str]:
    """扫描 `package` 下所有模块，注册其中的 `router`。

    Returns:
        已注册的模块名列表（便于启动日志与自检）。
    """
    try:
        pkg = importlib.import_module(package)
    except ModuleNotFoundError:
        logger.warning("路由包 %s 不存在，跳过自动发现", package)
        return []

    registered: list[str] = []

    for mod_info in pkgutil.walk_packages(pkg.__path__, prefix=f"{package}."):
        name = mod_info.name
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - 单个模块导入失败不应拖垮整个服务
            logger.error("导入路由模块 %s 失败：%s", name, exc, exc_info=True)
            continue

        router = getattr(module, "router", None)
        if router is None:
            continue
        if not isinstance(router, APIRouter):
            logger.warning("模块 %s 的 router 不是 APIRouter，已跳过", name)
            continue

        app.include_router(router)
        registered.append(name)

    if registered:
        logger.info("自动注册 %d 个路由模块：%s", len(registered), ", ".join(registered))
    else:
        logger.info("未发现任何路由模块（app/api/ 下暂无 router）")

    return registered
