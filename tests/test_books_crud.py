"""图书 CRUD 接口测试：正常链路的完整闭环 + 参数校验 + 异常分支。

一条完整用例应该覆盖「操作 → 查询验证 → 清理」的闭环，而不是只断言状态码。
所以下面的主链路用例会：新增 → 查详情 → 更新 → 再查 → 删除 → 删后不可查。
"""

from __future__ import annotations

import pytest

from common.checks import check_schema, expect_error, expect_json, expect_schema, expect_status

BOOK_SCHEMA = {
    "id": int,
    "title": str,
    "author": str,
    "stock": int,
    "total": int,
}


# --------------------------------------------------------------------------- #
# 主链路
# --------------------------------------------------------------------------- #
def test_book_full_lifecycle(api, factory):
    """新增 → 查询 → 更新 → 删除 的完整闭环。"""
    created = factory.book(title="生命周期样本书", author="张三", stock=2)
    check_schema(created, BOOK_SCHEMA, path="$（新增图书）")
    assert created["stock"] == 2 and created["total"] == 2, "新增时 stock 与 total 应等于入参"

    fetched = expect_schema(api.get_book(created["id"]), BOOK_SCHEMA, note="查询刚新增的图书")
    assert fetched == created, "同一本书两次查询的返回应完全一致"

    updated = expect_schema(
        api.update_book(created["id"], {"title": "生命周期样本书（第 2 版）", "stock": 5}),
        BOOK_SCHEMA,
        note="更新图书",
    )
    assert updated["title"] == "生命周期样本书（第 2 版）"
    assert updated["stock"] == 5

    expect_status(api.delete_book(created["id"]), 204, note="删除图书")
    expect_error(api.get_book(created["id"]), 404, note="删除后再查询应返回 404")


def test_created_book_appears_in_list(api, factory):
    """新增后必须能在列表里查到——否则「写入成功但读不到」是典型的一致性问题。"""
    unique_title = "一致性校验专用书名-8848"
    book = factory.book(title=unique_title, stock=1)
    data = expect_json(api.list_books(keyword=unique_title), note="按书名搜索刚新增的图书")
    assert data["total"] == 1, f"应恰好搜到 1 条，实际 {data['total']} 条"
    assert data["items"][0]["id"] == book["id"]


# --------------------------------------------------------------------------- #
# 参数校验（等价类 + 边界值）
# --------------------------------------------------------------------------- #
@pytest.mark.boundary
@pytest.mark.parametrize(
    "payload,note",
    [
        ({"title": ""}, "书名空串"),
        ({"title": "     "}, "书名全为空白字符"),
        ({"title": "x" * 101}, "书名 101 字符，超出上限"),
        ({"title": "合法书名", "stock": -1}, "库存为 -1，低于下限"),
        ({"title": "合法书名", "stock": 1001}, "库存为 1001，超出上限"),
        ({"title": "合法书名", "stock": "三本"}, "库存为非数字字符串"),
        ({"title": "合法书名", "author": "x" * 51}, "作者 51 字符，超出上限"),
        ({"author": "只有作者没有书名"}, "缺少必填字段 title"),
        ({}, "请求体为空对象"),
    ],
)
def test_create_book_rejects_invalid_payload(api, payload, note):
    expect_error(api.create_book(payload), 422, note=note)


@pytest.mark.boundary
@pytest.mark.parametrize(
    "title,stock,note",
    [
        ("x" * 100, 0, "书名 100 字符（上边界）+ 库存 0（下边界）"),
        ("一", 1000, "书名 1 字符（下边界）+ 库存 1000（上边界）"),
        ("正常书名", 1, "普通取值"),
    ],
)
def test_create_book_accepts_boundary_values(factory, title, stock, note):
    book = factory.book(title=title, stock=stock)
    assert book["title"] == title, note
    assert book["stock"] == stock, note


def test_create_book_strips_surrounding_spaces(factory):
    """两端空白应被规范掉，否则会出现「同名却搜不到」的脏数据。"""
    book = factory.book(title="  前后有空格的书名  ")
    assert book["title"] == "前后有空格的书名"


# --------------------------------------------------------------------------- #
# 异常分支
# --------------------------------------------------------------------------- #
def test_get_unknown_book_returns_404(api):
    expect_error(api.get_book(999_999), 404, note="查询不存在的图书")


def test_update_unknown_book_returns_404(api):
    expect_error(api.update_book(999_999, {"title": "改一本不存在的书"}), 404, note="更新不存在的图书")


def test_delete_unknown_book_returns_404(api):
    expect_error(api.delete_book(999_999), 404, note="删除不存在的图书")


def test_update_with_empty_body_returns_422(api, factory):
    book = factory.book()
    expect_error(api.update_book(book["id"], {}), 422, note="更新请求体未包含任何字段")


def test_update_is_partial_and_keeps_other_fields(api, factory):
    """只传 title 时，author 与 stock 必须原样保留，不能被重置成默认值。"""
    book = factory.book(title="只改标题的书", author="原作者", stock=4)
    data = expect_json(api.update_book(book["id"], {"title": "改过的标题"}), note="只更新 title")
    assert data["title"] == "改过的标题"
    assert data["author"] == "原作者", "未传的字段被意外重置"
    assert data["stock"] == 4, "未传的字段被意外重置"


def test_update_rejects_invalid_stock(api, factory):
    book = factory.book(stock=3)
    expect_error(api.update_book(book["id"], {"stock": -1}), 422, note="更新为非法库存")
    assert expect_json(api.get_book(book["id"]))["stock"] == 3, "更新失败时原数据不应被修改"


def test_delete_is_not_idempotent_on_missing_resource(api, factory):
    """删除接口的两次调用语义不同：第一次 204，第二次 404（资源已不存在）。"""
    book = factory.book()
    expect_status(api.delete_book(book["id"]), 204, note="第一次删除")
    expect_error(api.delete_book(book["id"]), 404, note="第二次删除")


def test_delete_book_with_active_borrowing_returns_409(api, factory):
    """有未归还记录的图书不允许删除，否则借阅记录会变成孤儿数据。"""
    book = factory.book(title="有在借记录的图书", stock=1)
    reader = factory.reader()
    expect_status(api.borrow(book["id"], reader["id"]), 201, note="先把唯一一本借出去")
    expect_error(api.delete_book(book["id"]), 409, note="有未归还记录的图书不允许删除")


def test_book_can_be_deleted_after_all_copies_returned(api, factory):
    """借阅记录归还后，图书应恢复可删除。"""
    book = factory.book(title="归还后可删除的图书", stock=1)
    reader = factory.reader()
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201, note="借出")
    expect_status(api.return_book(borrowing["id"]), 200, note="归还")
    expect_status(api.delete_book(book["id"]), 204, note="归还后删除图书")
