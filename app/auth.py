"""令牌签发与校验。

刻意不引入 PyJWT 等第三方库：用标准库 hmac + base64 实现一个结构等价的
「载荷.签名」令牌。这样鉴权相关的每个失败分支（缺失 / 格式非法 / 签名被篡改 /
已过期 / 主体不存在）都有真实可触发的代码路径，测试用例才有意义。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import Header, HTTPException

SECRET = "dev-secret-not-for-production"
TTL_SECONDS = 3600

# 演示用账号，明文口令仅用于本地被测服务
USERS: dict[str, dict[str, str]] = {
    "admin": {"password": "admin123", "role": "admin"},
    "reader": {"password": "reader123", "role": "reader"},
}


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    digest = hmac.new(SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return _b64encode(digest)


def create_token(username: str, ttl: int = TTL_SECONDS) -> str:
    """签发令牌。ttl 允许为负数，用于构造「已过期」的测试数据。"""
    payload = {"sub": username, "exp": int(time.time()) + ttl}
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def decode_token(token: str) -> dict:
    """校验并解析令牌，任何不合法的情况都抛 ValueError（附中文原因）。"""
    parts = token.split(".")
    if len(parts) != 2:
        raise ValueError("令牌格式非法，应为 载荷.签名")
    body, signature = parts
    if not hmac.compare_digest(signature, _sign(body)):
        raise ValueError("令牌签名校验失败")
    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, TypeError) as exc:
        raise ValueError("令牌载荷无法解析") from exc
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("令牌已过期")
    return payload


def authenticate(username: str, password: str) -> dict | None:
    """校验账号口令，成功返回用户信息，失败返回 None。"""
    user = USERS.get(username)
    if user is None or user["password"] != password:
        return None
    return {"username": username, "role": user["role"]}


def current_user(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI 依赖：从 Authorization 请求头解析并校验 Bearer 令牌。"""
    if not authorization:
        raise HTTPException(status_code=401, detail="缺少 Authorization 请求头")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Authorization 必须为 Bearer 格式")
    token = token.strip()
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    subject = payload.get("sub")
    if subject not in USERS:
        raise HTTPException(status_code=401, detail="令牌对应的用户不存在")
    return {"username": subject, "role": USERS[subject]["role"]}
