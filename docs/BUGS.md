# 缺陷记录（Bug Log）

本文件记录被测服务 `app/` 在开发过程中暴露出来的 5 个缺陷。

每一条缺陷都满足三件事：

1. 有明确的现象、复现步骤和根因定位；
2. 有对应的修复（已合入 `app/`）；
3. 有**一条常驻的回归用例**（`tests/test_regression.py`），把修复锁住。

> 怎么证明这些回归用例不是摆设？项目里带了变异验证脚本：
> `python tools/verify_regression.py` 会把 5 条修复**逐个回退成有缺陷的写法**，再跑对应用例。
> 5 条全部会变红（实测退出码均为 1），说明用例真的能抓住缺陷，而不是"跟着代码写"。

## 汇总

| 编号 | 标题 | 严重程度 | 影响面 | 回归用例 |
| --- | --- | --- | --- | --- |
| BUG-001 | 借阅上限判断差一位，读者能借到第 6 本 | 高 | 业务规则失效 | `test_bug001_limit_plus_one_is_rejected` |
| BUG-002 | `page=0` 导致服务端 500 | 中 | 非法入参打成服务端错误 | `test_bug002_page_zero_returns_422` |
| BUG-003 | 重复归还同一记录，库存被重复累加 | 高 | 数据被凭空造出来 | `test_bug003_repeated_return_is_rejected` |
| BUG-004 | 全空白字符的书名能通过校验入库 | 中 | 脏数据入库 | `test_bug004_blank_title_is_rejected` |
| BUG-005 | 图书有已归还的历史记录时，删除接口 500 | 中高 | 功能不可用 + 外键悬挂 | `test_bug005_book_with_returned_history_can_be_deleted` |

---

## BUG-001 借阅上限判断差一位

**现象**：同一读者未归还图书上限业务规则是 5 本，实际能借到第 6 本。

**复现步骤**

1. 造 6 本各有库存的图书；
2. 用同一个读者连续借这 6 本书；
3. 第 6 次请求返回 `201`，而期望是 `400`。

**根因**：`app/main.py` 的 `borrow()` 里比较用的是 `>`，而"已达上限"是包含当前这一本的临界情况。

```python
# 缺陷写法
if _active_borrowings(conn, payload.reader_id) > MAX_ACTIVE_BORROWINGS:
# 修复后
if _active_borrowings(conn, payload.reader_id) >= MAX_ACTIVE_BORROWINGS:
```

**为什么回归用例要写两条**：修复差一位很容易从一个极端修到另一个极端。
`test_bug001_exactly_at_limit_is_allowed`（刚好借满 5 本必须成功）和
`test_bug001_limit_plus_one_is_rejected`（第 6 本必须被拒）成对存在，
把上下边界一起钉死——只写一条的话，把 `>=` 误改成 `>` 也能"测试通过"。

---

## BUG-002 page=0 导致服务端 500

**现象**：`GET /api/books?page=0` 返回 `500 Internal Server Error`，响应体是 `Internal Server Error`。

**根因**：页码没有做下界约束，`offset = (page - 1) * size = -10`，负的 `OFFSET` 直接让 SQLite 抛异常，
异常没有被业务代码兜住，最终变成 500。

```python
# 缺陷写法：没有下界约束
page: int = Query(default=1, description="页码，从 1 开始")
# 修复后
page: int = Query(default=1, ge=1, description="页码，从 1 开始")
```

**回归断言为什么不止一条**：把 `page=0` 改回 422 只是修了一个具体取值。
`test_bug002_invalid_pagination_never_returns_5xx` 断言的是**更本质的性质**：
任何非法分页参数只允许落在 4xx，绝不允许打到 5xx。这条断言对将来新增的非法取值同样有效。

---

## BUG-003 重复归还导致库存被重复累加

**现象**：同一条借阅记录连续调两次归还接口，两次都返回 `200`，图书库存被加了 2 次。

**复现步骤**

1. 造一本库存 2 的书；
2. 借出一本（库存 1）；
3. 调用 `/api/return` 归还 → `200`，库存回到 2；
4. 用**同一个** `borrowing_id` 再调一次 `/api/return` → `200`，库存变成 3。

**影响**：库存是凭空造出来的，且没有任何日志能看出异常。这是比"接口报错"严重得多的一类缺陷。

**根因**：归还逻辑只查了记录是否存在，没有校验记录当前状态。

```python
# 修复：先判断状态，幂等性靠接口拒绝来保证，而不是靠客户端自觉
if record["status"] == "returned":
    raise HTTPException(status_code=409, detail="该借阅记录已归还，请勿重复归还")
```

**回归断言分两层**：接口层断言第二次归还返回 `409`；
数据层（`test_bug003_stock_not_inflated_by_repeated_return`）直连 SQLite 断言库存没有被加出来。
只断接口返回码是不够的——有可能状态码对了、数据还是错的。

---

## BUG-004 全空白字符的书名能通过校验

**现象**：`POST /api/books` 传 `{"title": "   "}` 返回 `201`，库里多了一条书名为空白的图书。

**根因**：`title` 只声明了 `min_length=1, max_length=100`，
而 `"   "` 的长度是 3，长度校验完全放行。

```python
# 修复：加字段校验器，去空白后为空即非法
@field_validator("title")
@classmethod
def _reject_blank_title(cls, value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("title 不能只由空白字符组成")
    return stripped
```

**同类问题**：读者名的 `name` 字段有完全一样的缺陷，用同样的方式修掉了（`_reject_blank_name`）。

**回归断言同样分两层**：接口层断言 `"   "`、`"\t\n"`、`""` 三种输入都返回 `422`；
数据层断言库里不存在 `strip()` 之后为空的书名——防止将来从别的入口（比如 `PUT`）又漏进去。

---

## BUG-005 图书有已归还的历史记录时，删除接口 500

**现象**：`DELETE /api/books/{id}` 返回 `500 Internal Server Error`，
而按业务规则"只要没有未归还的借阅记录就能删"，期望是 `204`。

**发现方式**：这一条不是设计用例时想到的，而是**整套用例第一次全量运行时的唯一一条红灯**。
130 多条用例里它单独失败，报错信息 `期望状态码 204，实际 500` 直接把位置指到了删除接口。
这正好也是写自动化测试的价值所在：人手工点页面，很难点到"先借、再还、然后删这本书"这条路径。

**根因**：`borrowings.book_id` 的外键没有级联删除。业务代码已经用 `409` 挡住了"有未归还记录"的情况，
但只要该图书存在过任何借阅历史（哪怕已归还），`DELETE FROM books` 就会撞上外键约束抛 `IntegrityError`，
异常没被兜住 → 500。

```sql
-- 缺陷写法
book_id INTEGER NOT NULL REFERENCES books(id),
-- 修复后：删除图书时一并清理它的历史借阅记录
book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
```

**设计取舍**：也可以选择"只要有任何借阅历史就不允许删除图书"（改成返回 409）。
这里选了级联删除，理由是接口文档承诺的是"没有**未归还**记录即可删除"，
保持接口语义比让调用方替实现细节买单更合理。

**回归断言分两层**：接口层断言归还后能删除、删除后查询返回 404；
数据层断言删除图书后 `borrowings` 里没有悬挂记录（`COUNT(*) WHERE book_id = ?` 为 0）。
