"""借书 / 还书业务链路测试。

借还书是有状态的业务，断言必须同时看三处，缺一处就可能放过缺陷：
  1. 接口返回的借阅记录（状态、时间戳）
  2. 图书接口返回的库存
  3. 数据库里的真实库存
第 3 点是为了避免「接口自己证明自己」——服务端如果只在内存里改了值而没落库，
只断言接口返回的用例是发现不了的。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.main import MAX_ACTIVE_BORROWINGS
from common.checks import expect_error, expect_json, expect_schema, expect_status
from common.data_loader import load_cases

BORROWING_SCHEMA = {
    "id": int,
    "book_id": int,
    "reader_id": int,
    "status": ("borrowed", "returned"),
    "borrowed_at": str,
    "returned_at": (str, type(None)),
}


def stock_in_db(db_path, book_id: int) -> int:
    """直接查被测服务的数据库，绕过接口做交叉验证。"""
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT stock FROM books WHERE id = ?", (book_id,)).fetchone()
    finally:
        connection.close()
    assert row is not None, f"库中不存在 id={book_id} 的图书"
    return int(row[0])


# --------------------------------------------------------------------------- #
# 主链路
# --------------------------------------------------------------------------- #
def test_borrow_decreases_stock_in_api_and_database(api, factory, db_path):
    book = factory.book(title="借书库存校验", stock=3)
    reader = factory.reader()

    record = expect_schema(
        api.borrow(book["id"], reader["id"]), BORROWING_SCHEMA, 201, note="借书成功"
    )
    assert record["status"] == "borrowed"
    assert record["returned_at"] is None, "未归还的记录不应有归还时间"
    assert record["book_id"] == book["id"] and record["reader_id"] == reader["id"]

    assert expect_json(api.get_book(book["id"]))["stock"] == 2, "接口返回的库存未减 1"
    assert stock_in_db(db_path, book["id"]) == 2, "数据库中的库存未减 1"


def test_return_increases_stock_in_api_and_database(api, factory, db_path):
    book = factory.book(title="还书库存校验", stock=1)
    reader = factory.reader()
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201, note="先借出")

    record = expect_schema(
        api.return_book(borrowing["id"]), BORROWING_SCHEMA, note="还书成功"
    )
    assert record["status"] == "returned"
    assert record["returned_at"] is not None, "已归还的记录应写入归还时间"
    assert record["id"] == borrowing["id"]

    assert expect_json(api.get_book(book["id"]))["stock"] == 1, "归还后库存未恢复"
    assert stock_in_db(db_path, book["id"]) == 1, "数据库中库存未恢复"


def test_borrow_then_return_restores_all_original_state(api, factory, db_path):
    """借出再归还是闭环操作，结束后图书状态必须与操作前一致。"""
    book = factory.book(title="闭环校验图书", stock=2)
    before = expect_json(api.get_book(book["id"]))
    reader = factory.reader()

    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201)
    expect_json(api.return_book(borrowing["id"]), 200)

    assert expect_json(api.get_book(book["id"])) == before, "借还闭环后图书状态不一致"


def test_readers_borrowings_records_full_lifecycle(api, factory):
    """借阅记录查询要能反映状态流转：borrowed → returned，且可按状态过滤。"""
    book = factory.book(title="借阅记录查询专用书", stock=1)
    reader = factory.reader(name="借阅记录读者")
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201)

    borrowed = expect_json(api.reader_borrowings(reader["id"], status="borrowed"), note="过滤未归还")
    assert [item["id"] for item in borrowed["items"]] == [borrowing["id"]]
    assert borrowed["items"][0]["book_title"] == "借阅记录查询专用书"
    assert borrowed["items"][0]["reader_name"] == "借阅记录读者"

    expect_json(api.return_book(borrowing["id"]), 200, note="归还")

    still_borrowed = expect_json(api.reader_borrowings(reader["id"], status="borrowed"))
    assert still_borrowed["items"] == [], "归还后不应再出现在「未归还」过滤结果里"

    returned = expect_json(api.reader_borrowings(reader["id"], status="returned"))
    assert [item["id"] for item in returned["items"]] == [borrowing["id"]]

    all_records = expect_json(api.reader_borrowings(reader["id"]))
    assert all_records["total"] == 1, "不加过滤条件时应返回全部记录"


# --------------------------------------------------------------------------- #
# 借阅上限
# --------------------------------------------------------------------------- #
@pytest.mark.boundary
def test_borrow_limit_is_enforced(api, factory, borrower):
    """未归还图书达到上限后，再借必须被拒绝（缺陷回归见 BUG-001）。"""
    borrower["borrow"](MAX_ACTIVE_BORROWINGS)
    extra_book = factory.book(title="超出上限的第 N+1 本", stock=1)
    expect_error(
        api.borrow(extra_book["id"], borrower["reader"]["id"]),
        400,
        note=f"借第 {MAX_ACTIVE_BORROWINGS + 1} 本应被拒绝",
    )


def test_borrow_limit_counts_only_unreturned_books(api, factory, borrower):
    """归还一本后额度应释放——上限统计的是「未归还」的数量。"""
    borrowings = borrower["borrow"](MAX_ACTIVE_BORROWINGS)
    expect_status(api.return_book(borrowings[0]["id"]), 200, note="先归还一本")

    new_book = factory.book(title="额度释放后可借的书", stock=1)
    expect_status(
        api.borrow(new_book["id"], borrower["reader"]["id"]),
        201,
        note="归还后额度已释放，应可继续借",
    )


def test_borrow_limit_is_per_reader(api, factory, borrower):
    """上限是「单读者」维度，不能误伤其他读者。"""
    borrower["borrow"](MAX_ACTIVE_BORROWINGS)
    other_reader = factory.reader(name="不受上限影响的另一位读者")
    book = factory.book(title="另一位读者要借的书", stock=1)
    expect_status(api.borrow(book["id"], other_reader["id"]), 201, note="另一位读者仍可正常借书")


# --------------------------------------------------------------------------- #
# 异常分支
# --------------------------------------------------------------------------- #
def test_borrow_unknown_book_returns_404(api, factory):
    reader = factory.reader()
    expect_error(api.borrow(999_999, reader["id"]), 404, note="借一本不存在的图书")


def test_borrow_unknown_reader_returns_404(api, factory):
    book = factory.book(stock=1)
    expect_error(api.borrow(book["id"], 999_999), 404, note="不存在的读者来借书")


def test_borrow_out_of_stock_returns_409(api, factory):
    book = factory.book(title="库存为 0 的图书", stock=0)
    reader = factory.reader()
    expect_error(api.borrow(book["id"], reader["id"]), 409, note="库存为 0 时借书")


def test_all_copies_borrowed_then_next_reader_gets_409(api, factory):
    """库存刚好借完的场景：第 N+1 个读者应拿到 409，且库存不会变成负数。"""
    book = factory.book(title="刚好借完的书", stock=2)
    first = factory.reader(name="第一位读者")
    second = factory.reader(name="第二位读者")
    third = factory.reader(name="第三位读者")

    expect_status(api.borrow(book["id"], first["id"]), 201, note="第一位读者借走第 1 本")
    expect_status(api.borrow(book["id"], second["id"]), 201, note="第二位读者借走第 2 本")
    expect_error(api.borrow(book["id"], third["id"]), 409, note="库存已空，第三位读者被拒")

    assert expect_json(api.get_book(book["id"]))["stock"] == 0, "库存不应变成负数"


def test_return_twice_returns_409(api, factory):
    book = factory.book(stock=1)
    reader = factory.reader()
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201)
    expect_status(api.return_book(borrowing["id"]), 200, note="第一次归还")
    expect_error(api.return_book(borrowing["id"]), 409, note="重复归还")


def test_repeated_return_does_not_inflate_stock(api, factory, db_path):
    """重复归还若被错误地放行，库存会被凭空加出来——这里做数据一致性防护。"""
    book = factory.book(title="重复归还防护", stock=1)
    reader = factory.reader()
    borrowing = expect_json(api.borrow(book["id"], reader["id"]), 201)
    expect_json(api.return_book(borrowing["id"]), 200, note="正常归还")
    expect_error(api.return_book(borrowing["id"]), 409, note="重复归还被拒绝")
    assert stock_in_db(db_path, book["id"]) == 1, "重复归还被拒绝后库存不应被再次增加"


def test_return_unknown_record_returns_404(api):
    expect_error(api.return_book(999_999), 404, note="归还不存在的借阅记录")


def test_borrowings_of_unknown_reader_returns_404(api):
    expect_error(api.reader_borrowings(999_999), 404, note="查询不存在读者的借阅记录")


@pytest.mark.boundary
@pytest.mark.parametrize(
    "payload,note",
    [
        ({"book_id": 0, "reader_id": 1}, "book_id 为 0，低于下边界"),
        ({"book_id": 1, "reader_id": 0}, "reader_id 为 0，低于下边界"),
        ({"book_id": -1, "reader_id": 1}, "book_id 为负数"),
        ({"book_id": 1}, "缺少 reader_id"),
        ({}, "缺少全部字段"),
    ],
)
def test_borrow_rejects_invalid_payload(api, payload, note):
    expect_error(api.request("POST", "/api/borrow", json=payload), 422, note=note)


# --------------------------------------------------------------------------- #
# 数据驱动用例
# --------------------------------------------------------------------------- #
BORROW_CASES = load_cases("borrow_cases.yaml", "borrow_cases")
BORROW_CASE_IDS = [case["case_id"] for case in BORROW_CASES]


@pytest.mark.parametrize("case", BORROW_CASES, ids=BORROW_CASE_IDS)
def test_borrow_cases_from_yaml(api, factory, case):
    """数据驱动：用例表维护在 data/borrow_cases.yaml，新增场景只需改 YAML。"""
    reader = factory.reader(name=f"数据驱动读者-{case['case_id']}")
    reader_id = reader["id"] if case.get("reader_exists", True) else 999_999

    if case.get("book_exists", True):
        book_id = factory.book(title=f"数据驱动图书-{case['case_id']}", stock=case["book_stock"])["id"]
    else:
        book_id = 999_999

    response = api.borrow(book_id, reader_id)
    if case["expected_status"] >= 400:
        expect_error(response, case["expected_status"], note=case["title"])
    else:
        expect_status(response, case["expected_status"], note=case["title"])
