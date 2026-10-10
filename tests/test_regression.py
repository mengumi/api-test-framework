"""缺陷回归用例集。

每一条用例对应 docs/BUGS.md 里的一条缺陷记录：编号、根因、修复、回归用例一一对应。
这类用例的特点是不追求覆盖率，只追求「同一个坑不许再踩第二次」，
因此在缺陷修复后必须长期保留，不能被清理掉。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.main import MAX_ACTIVE_BORROWINGS
from common.checks import expect_error, expect_json, expect_status

pytestmark = pytest.mark.regression


def stock_in_db(db_path, book_id: int) -> int:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT stock FROM books WHERE id = ?", (book_id,)).fetchone()
    finally:
        connection.close()
    assert row is not None, f"库中不存在 id={book_id} 的图书"
    return int(row[0])


# --------------------------------------------------------------------------- #
# BUG-001：借阅上限判断差一位
# --------------------------------------------------------------------------- #
def test_bug001_exactly_at_limit_is_allowed(borrower):
    """上边界内的最后一个额度必须可用：借到第 5 本应成功。

    回归理由：修 BUG-001 时如果把 >= 写成 >，就会把「恰好借满」也挡掉，
    从一个缺陷修成另一个缺陷。所以两侧都要有用例。
    """
    records = borrower["borrow"](MAX_ACTIVE_BORROWINGS)
    assert len(records) == MAX_ACTIVE_BORROWINGS


def test_bug001_limit_plus_one_is_rejected(api, factory, borrower):
    """BUG-001 回归：借阅上限判断用了 > 而不是 >=，读者能借到第 6 本。

    修复位置：app/main.py 的 borrow() 中 `_active_borrowings(...) >= MAX_ACTIVE_BORROWINGS`。
    """
    borrower["borrow"](MAX_ACTIVE_BORROWINGS)
    extra = factory.book(title="BUG-001 回归：超出上限的书", stock=1)
    expect_error(
        api.borrow(extra["id"], borrower["reader"]["id"]),
        400,
        note=f"BUG-001 回归：借第 {MAX_ACTIVE_BORROWINGS + 1} 本必须被拒绝",
    )


# --------------------------------------------------------------------------- #
# BUG-002：page=0 导致 500
# --------------------------------------------------------------------------- #
def test_bug002_page_zero_returns_422(api):
    """BUG-002 回归：page=0 时 offset 被算成负数，SQLite 抛异常导致 500。

    修复位置：app/main.py 的 list_books() 用 Query(ge=1) 声明入参约束。
    """
    expect_error(api.list_books(page=0), 422, note="BUG-002 回归：page=0")


def test_bug002_invalid_pagination_never_returns_5xx(api):
    """更本质的回归断言：非法分页参数只能得到 4xx，不能打到服务端错误。"""
    for params in ({"page": 0}, {"page": -1}, {"size": 0}, {"size": 101}, {"page": 0, "size": 0}):
        response = api.list_books(**params)
        assert 400 <= response.status_code < 500, (
            f"BUG-002 回归：{params} 返回了 {response.status_code}，非法入参必须落在 4xx"
        )


# --------------------------------------------------------------------------- #
# BUG-003：重复归还导致库存被重复累加
# --------------------------------------------------------------------------- #
def test_bug003_repeated_return_is_rejected(api, factory):
    """BUG-003 回归：归还接口未校验借阅记录状态，同一条记录被归还两次。

    修复位置：app/main.py 的 return_book() 中先判断 `record['status'] == 'returned'` → 409。
    """
    book = factory.book(title="BUG-003 回归：重复归还", stock=1)
    reader = factory.reader()
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201)
    expect_status(api.return_book(borrowing["id"]), 200, note="第一次归还")
    expect_error(api.return_book(borrowing["id"]), 409, note="BUG-003 回归：第二次归还必须被拒绝")


def test_bug003_stock_not_inflated_by_repeated_return(api, factory, db_path):
    """数据层回归：即使 409 已被断言，仍要确认库存没有被凭空加出来。"""
    book = factory.book(title="BUG-003 回归：库存不得膨胀", stock=2)
    reader = factory.reader()
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201)
    expect_json(api.return_book(borrowing["id"]), 200, note="正常归还")
    api.return_book(borrowing["id"])  # 重复归还，静默忽略返回码，只关注数据结果
    assert stock_in_db(db_path, book["id"]) == 2, "BUG-003 回归：库存被重复归还加到了 3"


# --------------------------------------------------------------------------- #
# BUG-004：全空白字符的书名被写入数据库
# --------------------------------------------------------------------------- #
def test_bug004_blank_title_is_rejected(api):
    """BUG-004 回归：title 只校验了长度，长度为 1 的空格能通过校验并入库。

    修复位置：app/schemas.py 的 BookCreate._reject_blank_title 字段校验器。
    """
    expect_error(api.create_book({"title": "   "}), 422, note="BUG-004 回归：全空白书名")
    expect_error(api.create_book({"title": "\t\n"}), 422, note="BUG-004 回归：制表符与换行组成书名")
    expect_error(api.create_book({"title": ""}), 422, note="BUG-004 回归：空串书名")


def test_bug004_no_blank_titles_exist_in_database(api, factory, db_path):
    """数据层回归：库里不允许存在去掉空白后为空的书名。"""
    factory.book(title="BUG-004 回归：正常书名")
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT id, title FROM books").fetchall()
    finally:
        connection.close()
    offenders = [(row[0], row[1]) for row in rows if not row[1].strip()]
    assert offenders == [], f"BUG-004 回归：库中存在空白书名 {offenders}"


# --------------------------------------------------------------------------- #
# BUG-005：图书有已归还的历史记录时，删除接口返回 500
# --------------------------------------------------------------------------- #
def test_bug005_book_with_returned_history_can_be_deleted(api, factory):
    """BUG-005 回归：借阅历史的外键没有级联删除，删除图书时撞外键约束报 500。

    发现方式：本用例是跑完整套件时唯一一条红灯用例暴露出来的——删除「已全部归还」的图书
    返回 500 Internal Server Error，而不是 204。
    修复位置：app/db.py 的 borrowings 表 —— `book_id ... REFERENCES books(id) ON DELETE CASCADE`。
    """
    book = factory.book(title="BUG-005 回归：归还后可删除", stock=1)
    reader = factory.reader()
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201)
    expect_json(api.return_book(borrowing["id"]), 200, note="先把书还掉")

    expect_status(api.delete_book(book["id"]), 204, note="BUG-005 回归：归还后应能删除图书")
    expect_error(api.get_book(book["id"]), 404, note="删除后应查不到")


def test_bug005_borrowing_history_is_cascaded_not_orphaned(api, factory, db_path):
    """数据层回归：删除图书后不能留下悬挂的借阅记录，也不该残留外键冲突隐患。"""
    book = factory.book(title="BUG-005 回归：历史记录被级联清理", stock=1)
    reader = factory.reader()
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201)
    expect_json(api.return_book(borrowing["id"]), 200)

    expect_status(api.delete_book(book["id"]), 204)

    connection = sqlite3.connect(db_path)
    try:
        left = connection.execute(
            "SELECT COUNT(*) FROM borrowings WHERE book_id = ?", (book["id"],)
        ).fetchone()[0]
    finally:
        connection.close()
    assert left == 0, f"BUG-005 回归：图书已删除，但仍有 {left} 条借阅记录悬挂在库里"
