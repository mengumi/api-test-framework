"""读者接口测试：新增 / 查询 / 删除 以及「有借阅记录不允许删除」的业务约束。"""

from __future__ import annotations

import pytest

from common.checks import check_schema, expect_error, expect_json, expect_status

READER_SCHEMA = {"id": int, "name": str, "kind": ("student", "teacher", "public")}


def test_reader_full_lifecycle(api, factory):
    reader = factory.reader(name="生命周期读者", kind="teacher")
    check_schema(reader, READER_SCHEMA, path="$（新增读者）")
    assert reader["name"] == "生命周期读者"
    assert reader["kind"] == "teacher"

    expect_status(api.delete_reader(reader["id"]), 204, note="删除读者")
    expect_error(api.delete_reader(reader["id"]), 404, note="重复删除应 404")


def test_created_reader_appears_in_list(api, factory):
    reader = factory.reader(name="列表可见性校验读者")
    data = expect_json(api.list_readers(), note="读者列表")
    assert reader["id"] in [item["id"] for item in data["items"]], "新增的读者未出现在列表中"


@pytest.mark.parametrize("kind", ["student", "teacher", "public"])
def test_reader_accepts_each_declared_kind(factory, kind):
    """枚举型字段的每个合法取值都要有对应用例（等价类里每个有效类各取一个）。"""
    reader = factory.reader(name=f"{kind} 类型读者", kind=kind)
    assert reader["kind"] == kind


@pytest.mark.parametrize(
    "payload,note",
    [
        ({"name": ""}, "姓名为空串"),
        ({"name": "   "}, "姓名全为空白字符"),
        ({"name": "x" * 51}, "姓名超过 50 字符上限"),
        ({"name": "合法姓名", "kind": "vip"}, "读者类型不在枚举范围内"),
        ({"name": "合法姓名", "kind": "STUDENT"}, "读者类型大小写不匹配"),
        ({}, "缺少必填字段 name"),
    ],
)
def test_create_reader_rejects_invalid_payload(api, payload, note):
    expect_error(api.create_reader(payload), 422, note=note)


def test_delete_unknown_reader_returns_404(api):
    expect_error(api.delete_reader(999_999), 404, note="删除不存在的读者")


def test_delete_reader_with_borrowings_returns_409(api, factory):
    """有借阅历史的读者不能直接删除，否则历史记录会变成孤儿数据。"""
    reader = factory.reader(name="有借阅记录的读者")
    book = factory.book(stock=1)
    expect_status(api.borrow(book["id"], reader["id"]), 201, note="先产生一条借阅记录")
    expect_error(api.delete_reader(reader["id"]), 409, note="有借阅记录的读者不允许删除")
