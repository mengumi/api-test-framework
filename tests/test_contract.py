"""接口契约测试。

契约测试关注的是「接口长什么样」，而不是「业务对不对」：
  字段是否齐全、类型是否正确、枚举取值是否收敛、有没有多出不该出现的字段。
它的价值在于——服务端某天顺手把字段改名或去掉了，业务用例可能仍然全绿，
但前端会直接崩掉；契约用例是唯一能挡住这种改动的东西。
"""

from __future__ import annotations

import pytest

from common.checks import check_schema, expect_json, expect_schema, expect_status

pytestmark = pytest.mark.contract

LOGIN_SCHEMA = {
    "access_token": str,
    "token_type": str,
    "expires_in": int,
    "username": str,
    "role": str,
}

BOOK_SCHEMA = {
    "id": int,
    "title": str,
    "author": str,
    "stock": int,
    "total": int,
}

BOOK_LIST_SCHEMA = {"total": int, "page": int, "size": int, "items": [BOOK_SCHEMA]}

READER_SCHEMA = {"id": int, "name": str, "kind": ("student", "teacher", "public")}
READER_LIST_SCHEMA = {"total": int, "items": [READER_SCHEMA]}

BORROWING_SCHEMA = {
    "id": int,
    "book_id": int,
    "reader_id": int,
    "status": ("borrowed", "returned"),
    "borrowed_at": str,
    "returned_at": (str, type(None)),
}

BORROWING_LIST_SCHEMA = {
    "total": int,
    "items": [{**BORROWING_SCHEMA, "book_title": str, "reader_name": str}],
}

ERROR_SCHEMA = {"detail": str}
VALIDATION_ERROR_SCHEMA = {"detail": [{"type": str, "loc": list, "msg": str}]}


# --------------------------------------------------------------------------- #
# 各类响应体结构
# --------------------------------------------------------------------------- #
def test_login_response_contract(anon):
    expect_schema(anon.login("admin", "admin123"), LOGIN_SCHEMA, note="登录响应结构")


def test_book_response_contract(api, factory):
    note_suffix = "新增图书响应结构"
    expect_schema(api.create_book({"title": "契约校验样本书"}), BOOK_SCHEMA, 201, note=note_suffix)
    book = factory.book(title="契约校验样本书-2")
    expect_schema(api.get_book(book["id"]), BOOK_SCHEMA, note="图书详情响应结构")
    expect_schema(api.update_book(book["id"], {"stock": 7}), BOOK_SCHEMA, note="更新图书响应结构")


def test_book_list_response_contract(api):
    expect_schema(api.list_books(), BOOK_LIST_SCHEMA, note="图书列表响应结构")


def test_empty_book_list_still_matches_contract(api):
    """空结果也必须是合法结构（items 为空数组），不能返回 null 或直接省略字段。"""
    data = expect_schema(
        api.list_books(keyword="绝对搜不到的关键字-zzzz"), BOOK_LIST_SCHEMA, note="空列表响应结构"
    )
    assert data["items"] == []


def test_reader_response_contract(api, factory):
    expect_schema(api.create_reader({"name": "契约校验读者"}), READER_SCHEMA, 201, note="新增读者响应结构")


def test_reader_list_response_contract(api):
    expect_schema(api.list_readers(), READER_LIST_SCHEMA, note="读者列表响应结构")


def test_borrowing_response_contract(api, factory):
    book = factory.book(stock=1)
    reader = factory.reader()
    borrowing = expect_schema(api.borrow(book["id"], reader["id"]), BORROWING_SCHEMA, 201, note="借书响应结构")
    expect_schema(
        api.return_book(borrowing["id"]), BORROWING_SCHEMA, note="还书响应结构"
    )


def test_reader_borrowings_response_contract(api, factory):
    book = factory.book(stock=1, title="借阅记录契约书")
    reader = factory.reader(name="借阅记录契约读者")
    expect_status(api.borrow(book["id"], reader["id"]), 201)
    expect_schema(
        api.reader_borrowings(reader["id"]), BORROWING_LIST_SCHEMA, note="借阅记录列表响应结构"
    )


# --------------------------------------------------------------------------- #
# 错误响应结构
# --------------------------------------------------------------------------- #
def test_not_found_error_contract(api):
    expect_schema(api.get_book(999_999), ERROR_SCHEMA, 404, note="404 错误响应结构")


def test_conflict_error_contract(api, factory):
    book = factory.book(stock=1)
    reader = factory.reader()
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201)
    expect_status(api.return_book(borrowing["id"]), 200)
    expect_schema(api.return_book(borrowing["id"]), ERROR_SCHEMA, 409, note="409 错误响应结构")


def test_unauthorized_error_contract(anon):
    expect_schema(
        anon.create_book({"title": "未鉴权"}), ERROR_SCHEMA, 401, note="401 错误响应结构"
    )


def test_validation_error_contract(api):
    """FastAPI 的 422 结构是「错误列表」，前端会按 loc 定位到具体字段，不能随意改动。"""
    expect_schema(
        api.create_book({"title": ""}), VALIDATION_ERROR_SCHEMA, 422, note="422 校验错误响应结构"
    )


def test_validation_error_points_to_offending_field(api):
    data = expect_json(api.create_book({"title": "合法书名", "stock": -1}), 422, note="库存非法")
    locations = [item["loc"] for item in data["detail"]]
    assert any("stock" in loc for loc in locations), f"422 未定位到出错的字段 stock：{locations}"


def test_error_responses_never_leak_stack_trace(api):
    """错误信息里出现 Traceback / 文件路径，说明异常没被兜住，属于信息泄露。"""
    for method, path, payload, expected in (
        ("GET", "/api/books/999999", None, 404),
        ("DELETE", "/api/books/999999", None, 404),
        ("POST", "/api/books", {"title": ""}, 422),
    ):
        response = api.request(method, path, json=payload)
        expect_status(response, expected, note=f"{method} {path}")
        body = response.text
        assert "Traceback" not in body, f"响应泄露了堆栈信息：{body[:300]}"
        assert "File \"" not in body, f"响应疑似泄露了源码路径：{body[:300]}"


# --------------------------------------------------------------------------- #
# 字段集合与信息泄露
# --------------------------------------------------------------------------- #
def test_book_response_has_exactly_the_contracted_fields(api, factory):
    """字段集合要显式收敛，防止内部字段（如 created_at）被顺手透出去。"""
    book = factory.book()
    assert set(book) == set(BOOK_SCHEMA), f"图书响应字段与契约不一致：{sorted(book)}"


def test_login_response_never_returns_password(anon):
    data = expect_json(anon.login("admin", "admin123"), note="登录响应")
    assert "password" not in data, "登录响应不应包含 password 字段"
    assert "admin123" not in str(data), "登录响应泄露了口令明文"


def test_response_content_type_is_json(api, anon):
    for response, expected, note in (
        (api.list_books(), 200, "图书列表"),
        (anon.login("admin", "admin123"), 200, "登录"),
        (api.get_book(999_999), 404, "404 错误"),
    ):
        expect_status(response, expected, note=note)
        assert response.headers.get("Content-Type", "").startswith("application/json"), (
            f"{note} 的 Content-Type 应为 application/json，实际为 {response.headers.get('Content-Type')}"
        )


def test_delete_returns_no_body(api, factory):
    """204 语义要求不返回响应体，返回了就是契约不严谨。"""
    book = factory.book()
    response = api.delete_book(book["id"])
    expect_status(response, 204, note="删除图书")
    assert response.content == b"", f"204 响应不应带响应体：{response.content!r}"


def test_contract_helpers_catch_type_mismatch():
    """给框架自身写一条用例：契约校验器必须真的能发现类型错误，否则契约用例是假绿。"""
    from common.checks import check_schema

    check_schema({"id": 1}, {"id": int})  # 正常情况不抛异常

    with pytest.raises(AssertionError):
        check_schema({"id": "1"}, {"id": int})

    with pytest.raises(AssertionError):
        check_schema({"id": True}, {"id": int})  # bool 不能被当成 int 放过

    with pytest.raises(AssertionError):
        check_schema({}, {"id": int})  # 缺字段

    with pytest.raises(AssertionError):
        check_schema({"status": "lost"}, {"status": ("borrowed", "returned")})

    with pytest.raises(AssertionError):
        check_schema({"returned_at": "2026-01-01"}, {"returned_at": (int, type(None))})
