"""后台任务包 —— WP-BE-08 F9。

`app/api/` 放的是**请求驱动**的代码；本包放**时间驱动**的代码。
统一入口是 `scheduler.start_background_tasks()`，由 `app/main.py` 的 lifespan 启停。
"""

from app.tasks.scheduler import (
    run_daily_job_now,
    run_startup_checks,
    start_background_tasks,
    stop_background_tasks,
)

__all__ = [
    "run_daily_job_now",
    "run_startup_checks",
    "start_background_tasks",
    "stop_background_tasks",
]
