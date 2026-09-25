"""结构化日志 —— 契约 §3 / 10a F9。

要求：
- 输出包含：时间 / 级别 / trace_id / 模块 / 消息 / 异常栈
- `LOG_LEVEL` 可配（DEBUG/INFO/WARNING/ERROR）
- 同时写文件与控制台
- **禁止 `print`**（禁止清单第 9 条）
"""

from __future__ import annotations

import logging
import logging.handlers
import time
from pathlib import Path

from app.core.trace import get_trace_id

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(trace_id)s | %(name)s | %(message)s"

_configured = False


class TraceIdFilter(logging.Filter):
    """把当前请求的 trace_id 注入每条日志。非请求上下文填 `-`。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.trace_id = get_trace_id() or "-"
        return True


class StructuredFormatter(logging.Formatter):
    """UTC ISO 8601 时间 + 固定字段顺序，便于日志检索与按 trace_id 串联。"""

    converter = time.gmtime  # 全部按 UTC，与数据库时间口径一致

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z"


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """初始化根 logger。应在应用启动最早期调用一次。

    Args:
        level: 日志级别字符串，来自 .env 的 `LOG_LEVEL`。
        log_dir: 日志目录；为 None 时只输出到控制台。
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 清掉 uvicorn 默认 handler 之外的杂项，避免重复输出
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = StructuredFormatter(_LOG_FORMAT)
    trace_filter = TraceIdFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(trace_filter)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "bjsys.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(trace_filter)
        root.addHandler(file_handler)

    # 降低第三方库噪音
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """业务代码统一用 `get_logger(__name__)`。"""
    return logging.getLogger(name)
