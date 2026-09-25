"""鉴权：JWT 与口令哈希 —— 契约《01-共享契约冻结》§5.2 / 10a F10。

- JWT 算法固定 HS256，payload 固定包含 `sub`(user_id) / `role` / `exp` / `iat`。
- 口令用 bcrypt；**禁止任何形式的明文或可逆加密**。
- Token 有效期默认 12 小时（`.env` 的 `JWT_TTL_HOURS` 可配）——学生端设备长期使用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import BizError, ErrorCode

ALGORITHM = "HS256"

# ---- 口令哈希：直接使用 bcrypt 原生 API ----
# ⚠️ 不使用 passlib：passlib 1.7.4（2020 年后停止维护）与 bcrypt >= 4.1 不兼容 ——
#    它初始化时会跑一段自检代码并传入超过 72 字节的密码，新版 bcrypt 直接抛
#    `ValueError: password cannot be longer than 72 bytes`，导致整个 CryptContext 无法使用。
#    改为直接调用 bcrypt，依赖更少、行为更明确，对外接口保持不变。
#
# bcrypt 5.x 对超长密码**不再静默截断**而是抛异常，故这里显式截断到 72 字节。
# 项目密码策略为 6~20 位，即使全中文也不会触及上限。
_BCRYPT_ROUNDS = 12
_BCRYPT_MAX_BYTES = 72


def _to_bytes(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    """生成口令哈希（bcrypt，cost=12）。"""
    return bcrypt.hashpw(_to_bytes(plain), bcrypt.gensalt(_BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """校验口令。任何内部异常都视为校验失败，不向外泄露细节。"""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("ascii"))
    except Exception:  # noqa: BLE001 - 哈希格式损坏等情况一律判失败
        return False


def create_access_token(*, user_id: int, role: str) -> tuple[str, int]:
    """签发 JWT。

    Returns:
        (token, expires_in_seconds)
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    ttl_seconds = int(settings.JWT_TTL_HOURS) * 3600

    payload = {
        "sub": str(user_id),  # PyJWT 要求 sub 为字符串
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)
    return token, ttl_seconds


def decode_token(token: str) -> dict:
    """校验并解出 payload。过期、签名错、格式错一律抛 `10002`。"""
    if not token:
        raise BizError(ErrorCode.UNAUTHORIZED)
    try:
        return jwt.decode(token, get_settings().JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:  # 覆盖过期与签名错误
        raise BizError(ErrorCode.UNAUTHORIZED) from exc
