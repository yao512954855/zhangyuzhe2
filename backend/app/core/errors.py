"""统一错误码与业务异常 —— 契约《01-共享契约冻结》§3。

设计要点：
- 业务失败一律 **HTTP 200 + 非零 code**（契约 §5.1），前端在 apiClient 统一拦截。
- HTTP 非 200 仅用于：400 格式错 / 401 未登录 / 403 无权限 / 404 路径不存在 /
  413 文件过大 / 429 限流 / 500 服务异常。
- 错误码分段：10000 通用 / 20000 账号班级 / 30000 内容 / 40000 导入 /
  50000 语音 / 60000 服务发现 / 70000 二期学习（2026-09-25 追加）。
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    """全项目错误码常量（契约 §3 冻结，**不得增删改取值**）。"""

    # ---- 10000 段：通用 ----
    PARAM_INVALID = 10001
    UNAUTHORIZED = 10002
    FORBIDDEN = 10003
    NOT_FOUND = 10004
    TOO_MANY_REQUESTS = 10005
    INTERNAL_ERROR = 10006

    # ---- 20000 段：账号与班级 ----
    USERNAME_EXISTS = 20001
    BAD_CREDENTIALS = 20002
    ACCOUNT_DISABLED = 20003
    CLASS_NOT_FOUND = 20004

    # ---- 30000 段：内容 ----
    UNIT_NOT_FOUND = 30001
    CONTENT_NOT_FOUND = 30002
    CONTENT_HAS_RECORDS = 30003  # 有记录不可删 —— 设计约束，非异常
    CONTENT_INACTIVE = 30004

    # ---- 40000 段：导入 ----
    FILE_FORMAT_INVALID = 40001
    TEMPLATE_COLUMN_MISSING = 40002
    IMPORT_VALIDATION_FAILED = 40003
    FILE_TOO_LARGE = 40004

    # ---- 50000 段：语音 ----
    ASR_UNAVAILABLE = 50001
    ASR_TIMEOUT = 50002
    ASR_EMPTY = 50003  # 不计错
    ASR_BAD_AUDIO = 50004

    # ---- 60000 段：服务发现 ----
    DISCOVERY_FAILED = 60001

    # ---- 70000 段：二期学习能力（2026-09-25 追加，见 dev/50-二期工作包拆解）----
    PLAN_NOT_FOUND = 70001  # 练习方案不存在 / 越权（同码不暴露存在性）
    HOMEWORK_NOT_FOUND = 70002
    HOMEWORK_EXPIRED = 70003
    SRS_PLAN_NOT_FOUND = 70004
    VERSION_NOT_FOUND = 70005
    HINT_EXHAUSTED = 70006  # 提示档位用尽
    HINT_NOT_ALLOWED = 70007  # 该题型无提示


# 错误码 → 默认中文提示。
# 文案面向中小学学生与管理者，**禁用技术术语**（UI 规范文案要求）。
DEFAULT_MESSAGES: dict[int, str] = {
    ErrorCode.PARAM_INVALID: "填写的内容不正确，请检查后重试",
    ErrorCode.UNAUTHORIZED: "登录已过期，请重新登录",
    ErrorCode.FORBIDDEN: "没有操作权限",
    ErrorCode.NOT_FOUND: "请求的内容不存在",
    ErrorCode.TOO_MANY_REQUESTS: "操作太频繁了，请稍后再试",
    ErrorCode.INTERNAL_ERROR: "服务出了点问题，请稍后再试",
    ErrorCode.USERNAME_EXISTS: "该学号已被注册，请直接登录",
    ErrorCode.BAD_CREDENTIALS: "学号或密码不正确",
    ErrorCode.ACCOUNT_DISABLED: "账号已被停用，请联系管理者",
    ErrorCode.CLASS_NOT_FOUND: "班级不存在",
    ErrorCode.UNIT_NOT_FOUND: "单元不存在",
    ErrorCode.CONTENT_NOT_FOUND: "内容不存在",
    ErrorCode.CONTENT_HAS_RECORDS: "该内容已有练习记录，不能删除，只能停用",
    ErrorCode.CONTENT_INACTIVE: "该内容已停用",
    ErrorCode.FILE_FORMAT_INVALID: "文件格式不正确，请上传 Excel（xlsx）文件",
    ErrorCode.TEMPLATE_COLUMN_MISSING: "文件缺少必需的列，请下载最新模板",
    ErrorCode.IMPORT_VALIDATION_FAILED: "导入内容校验未通过",
    ErrorCode.FILE_TOO_LARGE: "文件太大了，请拆分后再上传",
    ErrorCode.ASR_UNAVAILABLE: "语音服务暂时不可用",
    ErrorCode.ASR_TIMEOUT: "语音识别超时",
    ErrorCode.ASR_EMPTY: "没有听到声音",
    ErrorCode.ASR_BAD_AUDIO: "录音格式不正确",
    ErrorCode.DISCOVERY_FAILED: "没能找到服务器",
    ErrorCode.PLAN_NOT_FOUND: "练习方案不存在",
    ErrorCode.HOMEWORK_NOT_FOUND: "作业不存在",
    ErrorCode.HOMEWORK_EXPIRED: "作业已截止",
    ErrorCode.SRS_PLAN_NOT_FOUND: "没有需要复习的内容",
    ErrorCode.VERSION_NOT_FOUND: "版本记录不存在",
    ErrorCode.HINT_EXHAUSTED: "提示已经用完了",
    ErrorCode.HINT_NOT_ALLOWED: "这道题不能使用提示",
}


class BizError(Exception):
    """业务异常。

    抛出后由 `main.py` 的全局 handler 统一转成
    `{"code": ..., "message": ..., "data": ..., "trace_id": ...}`。

    Args:
        code: 见 `ErrorCode`。
        message: 自定义提示；不传则取 `DEFAULT_MESSAGES`。
        http_status: 默认 200（契约 §5.1）。
        data: 附加数据（如 `CONTENT_HAS_RECORDS` 需带 `record_count`）。
    """

    def __init__(
        self,
        code: int,
        message: str | None = None,
        http_status: int = 200,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or DEFAULT_MESSAGES.get(code, "操作失败")
        self.http_status = http_status
        self.data = data
        super().__init__(self.message)

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"BizError(code={self.code}, message={self.message!r})"
