"""鉴权接口测试：登录、令牌校验、以及各写接口的鉴权边界。

测试思路：
  - 正常登录 + 返回结构契约
  - 等价类划分：密码错误 / 用户不存在 / 用户名大小写不同
  - 边界值：用户名口令为空串、口令超长
  - 令牌异常：缺失 / 认证方案错误 / 签名被篡改 / 主体不存在 / 已过期
  - 权限边界：写接口一律要求鉴权，读接口按设计对匿名开放
"""

from __future__ import annotations

import pytest

from app.auth import create_token
from common.checks import expect_error, expect_json, expect_schema, expect_status

pytestmark = pytest.mark.smoke

LOGIN_SCHEMA = {
    "access_token": str,
    "token_type": str,
    "expires_in": int,
    "username": str,
    "role": str,
}


# --------------------------------------------------------------------------- #
# 健康检查
# --------------------------------------------------------------------------- #
def test_health_check(anon):
    """服务必须可探活，这是所有后续用例的前置条件。"""
    assert expect_json(anon.health(), note="健康检查") == {"status": "ok"}


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
def test_login_returns_usable_token(anon):
    """登录成功后签发的令牌必须能通过签名校验，并带出身份信息。"""
    data = expect_schema(anon.login("admin", "admin123"), LOGIN_SCHEMA, note="管理员登录")
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert data["username"] == "admin"
    # 令牌结构为 载荷.签名，只有一段点号分隔
    assert data["access_token"].count(".") == 1, f"令牌结构异常：{data['access_token']}"


@pytest.mark.parametrize(
    "username,password,expected,note",
    [
        ("admin", "admin123!", 401, "密码错误"),
        ("admin", "ADMIN123", 401, "密码大小写敏感"),
        ("root", "admin123", 401, "用户不存在"),
        ("ADMIN", "admin123", 401, "用户名大小写敏感"),
        ("admin", "", 422, "密码为空串（入参校验拦截）"),
        ("", "admin123", 422, "用户名为空串（入参校验拦截）"),
        ("admin", "x" * 65, 422, "密码超过 64 字符上限"),
        ("x" * 33, "admin123", 422, "用户名超过 32 字符上限"),
    ],
)
def test_login_rejects_invalid_credentials(anon, username, password, expected, note):
    expect_error(anon.login(username, password), expected, note=note)


def test_login_error_message_is_readable(anon):
    """错误提示不能是内部异常信息，否则既暴露实现细节又不利于定位。"""
    data = expect_error(anon.login("admin", "wrong"), 401, note="登录失败提示")
    assert "密码" in data["detail"], f"错误提示不明确：{data['detail']}"
    assert "Traceback" not in str(data["detail"]), "错误提示泄露了内部堆栈"


# --------------------------------------------------------------------------- #
# 令牌异常（每一条都对应真实的令牌伪造 / 失效场景）
# --------------------------------------------------------------------------- #
def test_write_request_without_token_is_rejected(anon):
    expect_error(anon.create_book({"title": "无令牌新增"}), 401, note="未携带 Authorization 请求头")


def test_non_bearer_scheme_is_rejected(anon):
    anon.session.headers["Authorization"] = "Token deadbeef"
    expect_error(anon.create_book({"title": "错误认证方案"}), 401, note="认证方案不是 Bearer")


def test_tampered_signature_is_rejected(anon, admin_token):
    """保留合法载荷、只改签名——服务端必须靠签名校验挡住（而不是靠有没有点号）。"""
    body, _, signature = admin_token.partition(".")
    replacement = "A" if signature[0] != "A" else "B"
    anon.set_token(f"{body}.{replacement}{signature[1:]}")
    expect_error(anon.create_book({"title": "签名被篡改"}), 401, note="篡改令牌签名")


def test_tampered_payload_is_rejected(anon, admin_token):
    """改动载荷（提权成 admin）而不重算签名，同样必须被拒绝。"""
    body, _, signature = admin_token.partition(".")
    forged = body[:-1] + ("A" if body[-1] != "A" else "B")
    anon.set_token(f"{forged}.{signature}")
    expect_error(anon.create_book({"title": "载荷被篡改"}), 401, note="篡改令牌载荷")


def test_token_of_unknown_subject_is_rejected(anon):
    """签名合法但用户已被删除 / 从未存在，也属于非法令牌。"""
    anon.set_token(create_token("ghost-user"))
    data = expect_error(anon.create_book({"title": "主体不存在"}), 401, note="令牌主体不存在")
    assert "用户不存在" in data["detail"]


def test_expired_token_is_rejected(anon):
    """用负的 ttl 直接构造一个已过期的合法签名令牌。"""
    anon.set_token(create_token("admin", ttl=-60))
    data = expect_error(anon.create_book({"title": "过期令牌"}), 401, note="令牌已过期")
    assert "过期" in data["detail"], f"应提示令牌过期，实际提示：{data['detail']}"


def test_malformed_token_is_rejected(anon):
    anon.set_token("this-is-not-a-token")
    expect_error(anon.create_book({"title": "格式非法令牌"}), 401, note="令牌缺少签名段")


# --------------------------------------------------------------------------- #
# 权限边界
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/api/books", {"title": "未鉴权新增图书"}),
        ("PUT", "/api/books/1", {"title": "未鉴权更新图书"}),
        ("DELETE", "/api/books/1", None),
        ("POST", "/api/readers", {"name": "未鉴权新增读者"}),
        ("DELETE", "/api/readers/1", None),
        ("POST", "/api/borrow", {"book_id": 1, "reader_id": 1}),
        ("POST", "/api/return", {"borrowing_id": 1}),
    ],
)
def test_write_endpoints_require_authentication(anon, method, path, payload):
    """写操作必须全部落在鉴权之后，任何一个漏掉都是越权漏洞。"""
    expect_error(anon.request(method, path, json=payload), 401, note=f"{method} {path} 应要求鉴权")


def test_read_endpoints_are_public(anon):
    """按设计，图书查询对匿名用户开放——这条断言把「有意为之」固化成契约。"""
    expect_status(anon.list_books(), 200, note="图书列表允许匿名访问")
    expect_status(anon.get_book(1), 200, note="图书详情允许匿名访问")
