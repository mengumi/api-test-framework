"""图书列表分页与搜索接口测试。

分页是接口测试的经典高频考点，必须覆盖：
  - 首页 / 中间页 / 末页（可能不满一页）/ 超出总页数
  - 页与页之间不重复、不遗漏（用 id 集合验证，而不是只看条数）
  - total 表示「满足条件的总数」，与当前页返回的条数无关
  - page / size 的边界值与非法值
为了不受其他用例造出的数据干扰，本模块为每次用例生成唯一的关键字，
只统计自己造出来的那批数据。
"""

from __future__ import annotations

import uuid

import pytest

from common.checks import expect_error, expect_json, expect_status

pytestmark = pytest.mark.boundary

PAGE_SIZE_BOUNDARY = 7


@pytest.fixture
def keyword(factory) -> str:
    """造 7 条带唯一前缀的图书，返回该前缀。"""
    prefix = f"分页测试-{uuid.uuid4().hex[:8]}"
    for index in range(1, PAGE_SIZE_BOUNDARY + 1):
        factory.book(title=f"{prefix}-{index:02d}", stock=1)
    return prefix


def test_first_page_returns_requested_size(api, keyword):
    data = expect_json(api.list_books(page=1, size=3, keyword=keyword), note="第 1 页，每页 3 条")
    assert data["page"] == 1 and data["size"] == 3
    assert data["total"] == PAGE_SIZE_BOUNDARY, "total 应为满足条件的总数"
    assert len(data["items"]) == 3


def test_pages_do_not_overlap_or_lose_records(api, keyword):
    """把三页拼起来，必须恰好等于全集且无重复——只校验条数会漏掉这种缺陷。"""
    collected = []
    for page in (1, 2, 3):
        collected.extend(expect_json(api.list_books(page=page, size=3, keyword=keyword))["items"])
    ids = [item["id"] for item in collected]
    assert len(ids) == PAGE_SIZE_BOUNDARY, f"三页合计应有 {PAGE_SIZE_BOUNDARY} 条，实际 {len(ids)} 条"
    assert len(set(ids)) == len(ids), f"分页出现重复数据：{ids}"


def test_last_page_may_be_partial(api, keyword):
    data = expect_json(api.list_books(page=3, size=3, keyword=keyword), note="末页只应剩 1 条")
    assert data["total"] == PAGE_SIZE_BOUNDARY
    assert len(data["items"]) == 1, "7 条数据按每页 3 条分页，第 3 页应只有 1 条"


def test_page_beyond_total_returns_empty_list(api, keyword):
    data = expect_json(api.list_books(page=99, size=10, keyword=keyword), note="超出总页数")
    assert data["items"] == [], "超出范围的页码应返回空数组而不是报错"
    assert data["total"] == PAGE_SIZE_BOUNDARY, "total 不随页码变化"


def test_total_equals_item_count_when_single_page(api, keyword):
    data = expect_json(api.list_books(page=1, size=100, keyword=keyword), note="单页装下全部数据")
    assert data["total"] == len(data["items"]) == PAGE_SIZE_BOUNDARY


def test_default_pagination_params(api):
    """不传分页参数时应使用默认值（page=1, size=10）。"""
    data = expect_json(api.list_books(), note="使用默认分页参数")
    assert data["page"] == 1 and data["size"] == 10, f"默认分页参数被改动：page={data['page']}, size={data['size']}"


def test_keyword_filters_by_title(api, keyword):
    data = expect_json(api.list_books(keyword=keyword), note="按唯一关键字搜索")
    assert data["total"] == PAGE_SIZE_BOUNDARY
    assert all(keyword in item["title"] for item in data["items"]), "搜索结果中混入了不匹配的数据"


def test_keyword_without_match_returns_empty(api, keyword):
    data = expect_json(api.list_books(keyword=f"{keyword}-不存在的后缀"), note="无匹配结果")
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.parametrize(
    "params,note",
    [
        pytest.param({"page": 0}, "page=0 低于下边界（BUG-002 回归）", marks=pytest.mark.regression),
        ({"page": -1}, "page 为负数"),
        ({"page": "abc"}, "page 为非数字"),
        ({"page": 1.5}, "page 为小数"),
        ({"size": 0}, "size=0 低于下边界"),
        ({"size": 101}, "size=101 超过上边界"),
        ({"size": -5}, "size 为负数"),
        ({"size": 10_000}, "size 取值极大"),
    ],
)
def test_pagination_rejects_invalid_params(api, params, note):
    expect_error(api.list_books(**params), 422, note=note)


@pytest.mark.parametrize(
    "size,note",
    [
        (1, "size=1（下边界）"),
        (100, "size=100（上边界）"),
    ],
)
def test_pagination_accepts_boundary_size(api, keyword, size, note):
    data = expect_json(api.list_books(page=1, size=size, keyword=keyword), note=note)
    assert data["size"] == size
    assert len(data["items"]) == min(size, PAGE_SIZE_BOUNDARY)


def test_invalid_pagination_never_returns_server_error(api):
    """把一批非法入参集中打一遍，确认没有任何一个会打到 5xx。"""
    for params in ({"page": 0}, {"page": -1}, {"size": 0}, {"size": 101}, {"page": 0, "size": 0}):
        response = api.list_books(**params)
        assert response.status_code < 500, f"{params} 触发了服务端错误：{response.status_code} {response.text}"


def test_large_page_number_is_handled(api, keyword):
    """页码极大时不能因整数运算溢出或拼接出非法 SQL。"""
    expect_status(api.list_books(page=2**31 - 1, size=100, keyword=keyword), 200, note="超大页码")
