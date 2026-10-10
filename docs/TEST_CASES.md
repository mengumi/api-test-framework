# 测试用例设计说明

被测服务：`app/`（图书借阅 API，11 个接口，SQLite 存储）。
用例总数：**137 条**（由 91 个测试函数经参数化展开而来），全量运行约 9 秒。

```
tests/test_auth.py            26 条   鉴权与令牌
tests/test_books_crud.py      24 条   图书增删改查与入参校验
tests/test_books_pagination.py 20 条  分页与关键字搜索
tests/test_borrow_flow.py     25 条   借书/还书主流程与库存一致性
tests/test_contract.py        19 条   响应契约
tests/test_readers.py         13 条   读者管理
tests/test_regression.py      10 条   缺陷回归
```

## 一、用例分层（pytest marker）

不区分层次的自动化用例，跑起来只有一个"通过/失败"，出问题时分不清是核心链路断了还是边角料挂了。
所以按业务重要性和运行代价把用例分成四层，用 marker 标注，CI 里分阶段执行：

| marker | 条数 | 含义 | 什么时候跑 |
| --- | --- | --- | --- |
| `smoke` | 26 | 冒烟：服务能起来、能登录、鉴权没坏 | 每次提交，最先跑 |
| `boundary` | 38 | 边界与非法入参：等价类划分、边界值分析 | 每次提交 |
| `contract` | 19 | 契约：响应字段结构不能变 | 每次提交 |
| `regression` | 11 | 缺陷回归：曾经踩过的坑 | 每次提交，出现即阻塞 |

用例可以同时属于多层（例如"page=0 返回 422"既是 `boundary` 也是 `regression`）。

```bash
pytest -m smoke                 # 只跑冒烟
pytest -m "boundary or contract"
pytest -m "not regression"      # 临时跳过回归层
```

## 二、用例清单

### 1. `test_auth.py` — 鉴权与令牌（26 条，全量挂 `smoke`）

| 用例 | 覆盖点 | 设计方法 |
| --- | --- | --- |
| `test_health_check` | `/api/health` 返回 `{"status":"ok"}` | 基础可用性 |
| `test_login_returns_usable_token` | 登录返回 `access_token` / `token_type=bearer` / `expires_in` / `username` / `role`；令牌是"载荷.签名"两段结构 | 正向流程 |
| `test_login_rejects_invalid_credentials` | 密码错误、用户名不存在、密码大小写敏感 → 401；空密码、超长用户名/密码 → 422 | 等价类划分（参数化 8 组） |
| `test_login_error_message_is_readable` | 失败信息不暴露"是用户名错还是密码错"以外的细节 | 错误推测 |
| `test_write_request_without_token_is_rejected` | 无 `Authorization` 头 → 401 | 必填项缺失 |
| `test_non_bearer_scheme_is_rejected` | `Basic xxx` 等非 Bearer 方案 → 401 | 等价类 |
| `test_tampered_signature_is_rejected` | 改签名首字符（保证必定不同）→ 401 | 错误推测／安全 |
| `test_tampered_payload_is_rejected` | 改载荷末字符 → 401 | 错误推测／安全 |
| `test_token_of_unknown_subject_is_rejected` | 用合法密钥签一个不存在的用户 → 401 | 边界（身份不存在） |
| `test_expired_token_is_rejected` | `ttl=-60` 造过期令牌 → 401 | 边界（时间） |
| `test_malformed_token_is_rejected` | 非"载荷.签名"结构、空串、乱码 → 401 | 异常输入 |
| `test_write_endpoints_require_authentication` | 7 个写接口（POST/PUT/DELETE 图书、读者、借书、还书）无令牌一律 401 | 参数化遍历 |
| `test_read_endpoints_are_public` | 读接口对匿名开放 —— 这是**有意为之的设计**，用用例固化下来，避免将来被当成缺陷"修"掉 | 契约固化 |

> `test_expired_token_is_rejected` 直接 `import app.auth` 调 `create_token(ttl=-60)` 造令牌，
> 而不是去改系统时间或者在测试里等一小时。测试代码可以依赖被测模块的内部工具函数，只要目的是构造前置条件。

### 2. `test_books_crud.py` — 图书增删改查（24 条）

| 用例 | 覆盖点 | 设计方法 |
| --- | --- | --- |
| `test_book_full_lifecycle` | 新增 → 查询 → 修改 → 再查 → 删除 → 确认 404 的完整闭环 | 场景法 |
| `test_created_book_appears_in_list` | 新增后被列表接口按关键字搜到（写后读一致性） | 场景法 |
| `test_create_book_rejects_invalid_payload` | 9 组非法入参：空串、全空白、101 字符、stock 999/1001/非数字、author 51 字符、缺 title、空对象 | 等价类 + 边界值（参数化） |
| `test_create_book_accepts_boundary_values` | 书名 100 字符（上边界）、stock 0 与 1000（闭区间两端）都能成功 | 边界值分析 |
| `test_create_book_strips_surrounding_spaces` | 两端空白被规范化后再入库 | 错误推测 |
| `test_get_unknown_book_returns_404` | 不存在的 id → 404 | 等价类 |
| `test_update_unknown_book_returns_404` | 修改不存在的图书 → 404 | 等价类 |
| `test_delete_unknown_book_returns_404` | 删除不存在的图书 → 404 | 等价类 |
| `test_update_with_empty_body_returns_422` | PUT 空对象 → 422（而不是静默成功） | 错误推测 |
| `test_update_is_partial_and_keeps_other_fields` | **只传 stock 时 title/author 不允许被重置** | 状态迁移 |
| `test_update_rejects_invalid_stock` | 非法库存被拒后**原数据保持不变** | 事务性 |
| `test_delete_is_not_idempotent_on_missing_resource` | DELETE 不是幂等的：第一次 204，第二次 404 | 接口语义 |
| `test_delete_book_with_active_borrowing_returns_409` | 有未归还记录 → 409 | 业务规则 |
| `test_book_can_be_deleted_after_all_copies_returned` | 全部归还后可以删除（此用例暴露了 BUG-005） | 业务规则 + 状态迁移 |

### 3. `test_books_pagination.py` — 分页与搜索（20 条，全量挂 `boundary`）

夹具 `keyword` 一次造 7 条唯一前缀图书（`PAGE_SIZE_BOUNDARY = 7`），保证能被 3 页整除。

| 用例 | 覆盖点 | 设计方法 |
| --- | --- | --- |
| `test_first_page_returns_requested_size` | 首页条数等于 size | 边界值 |
| `test_pages_do_not_overlap_or_lose_records` | **三页拼接后 id 集合无重复、无遗漏**，且等于 total | 集合断言（比逐条比对更强） |
| `test_last_page_may_be_partial` | 末页不满时返回剩余条数，不报错 | 边界值 |
| `test_page_beyond_total_returns_empty_list` | 超出范围的页码返回空数组，total 不变 | 边界值 |
| `test_total_equals_item_count_when_single_page` | 单页容量内 `total == len(items)` | 一致性 |
| `test_default_pagination_params` | 不传参时 page=1、size=10 | 默认值 |
| `test_keyword_filters_by_title` | 关键字模糊匹配 | 等价类 |
| `test_keyword_without_match_returns_empty` | 无匹配返回空列表而非报错 | 边界 |
| `test_pagination_rejects_invalid_params` | 8 组非法分页（page=0/-1、size=0/101、组合）→ 422 | 边界值（参数化） |
| `test_pagination_accepts_boundary_size` | size=1 与 size=100（闭区间两端）成功 | 边界值 |
| `test_invalid_pagination_never_returns_server_error` | 非法分页只允许落 4xx，**绝不允许 5xx** | 性质断言 |
| `test_large_page_number_is_handled` | page=2³¹-1 返回 200 而不是整数溢出 500 | 极端边界 |

### 4. `test_borrow_flow.py` — 借还主流程与库存一致性（25 条）

核心手段：**接口断言 + 数据库断言交叉验证**。
辅助函数 `stock_in_db(db_path, book_id)` 直连 SQLite 读库存，和接口返回的 `stock` 对比。
只断言接口返回值，测的是"接口自洽"；加上数据层断言，才测到"数据真的变了"。

| 用例 | 覆盖点 | 设计方法 |
| --- | --- | --- |
| `test_borrow_decreases_stock_in_api_and_database` | 借书后：借阅记录存在 + 接口库存 -1 + 数据库库存 -1 | 三层交叉验证 |
| `test_return_increases_stock_in_api_and_database` | 还书后：状态变 returned + 接口库存 +1 + 数据库库存 +1 | 三层交叉验证 |
| `test_borrow_then_return_restores_all_original_state` | 借还一轮后图书完全恢复原状 | 状态迁移闭环 |
| `test_readers_borrowings_records_full_lifecycle` | 借阅记录的 `borrowed→returned` 状态流转，以及 `?status=` 过滤 | 状态迁移 |
| `test_borrow_limit_is_enforced` | 未归还上限 5 本 | 业务规则（`boundary`） |
| `test_borrow_limit_counts_only_unreturned_books` | 上限**只统计未归还**的书（还掉就释放额度） | 业务规则边界 |
| `test_borrow_limit_is_per_reader` | 上限按读者隔离，不同读者互不影响 | 业务规则边界 |
| `test_borrow_unknown_book_returns_404` | 图书不存在 → 404 | 等价类 |
| `test_borrow_unknown_reader_returns_404` | 读者不存在 → 404 | 等价类 |
| `test_borrow_out_of_stock_returns_409` | 库存为 0 → 409 | 业务规则 |
| `test_all_copies_borrowed_then_next_reader_gets_409` | 3 本全借完后第 4 位读者拿 409，**且库存不为负** | 边界 + 数据完整性 |
| `test_return_twice_returns_409` | 重复归还 → 409 | 幂等性 |
| `test_repeated_return_does_not_inflate_stock` | 重复归还后数据库库存不膨胀 | 数据完整性 |
| `test_return_unknown_record_returns_404` | 借阅记录不存在 → 404 | 等价类 |
| `test_borrowings_of_unknown_reader_returns_404` | 查不存在读者的借阅记录 → 404 | 等价类 |
| `test_borrow_rejects_invalid_payload` | 5 组非法入参（book_id=0/负数/非数字、reader_id 缺失等）→ 422 | 边界值（参数化） |
| `test_borrow_cases_from_yaml` | **数据驱动**：5 条场景从 `data/borrow_cases.yaml` 读取 | 数据驱动 |

### 5. `test_contract.py` — 响应契约（19 条，全量挂 `contract`）

为什么单独一层：业务用例全绿，字段名从 `book_id` 改成 `bookId` 前端照样崩。
契约测试不关心业务逻辑，只关心**响应结构**，字段改名的破坏性变更由它来挡。

| 用例 | 覆盖点 |
| --- | --- |
| `test_login_response_contract` | 登录响应结构与类型 |
| `test_book_response_contract` | 图书对象结构 |
| `test_book_list_response_contract` | 列表结构 `{total, page, size, items}` |
| `test_empty_book_list_still_matches_contract` | 空列表也是合法结构（不是 `null`） |
| `test_reader_response_contract` | 读者对象结构 |
| `test_reader_list_response_contract` | 读者列表结构 |
| `test_borrowing_response_contract` | 借阅记录结构；`status` 是枚举、`returned_at` 可为 `null` |
| `test_reader_borrowings_response_contract` | JOIN 后的记录额外含 `book_title` / `reader_name` |
| `test_not_found_error_contract` | 404 响应体为 `{"detail": str}` |
| `test_conflict_error_contract` | 409 响应体结构 |
| `test_unauthorized_error_contract` | 401 响应体结构 |
| `test_validation_error_contract` | 422 响应体为 `{"detail": [{type, loc, msg}, ...]}` |
| `test_validation_error_points_to_offending_field` | 422 能定位到具体字段（`loc` 里含出错的字段名） |
| `test_error_responses_never_leak_stack_trace` | 错误响应不泄露 `Traceback` / 源码路径 |
| `test_book_response_has_exactly_the_contracted_fields` | 字段集合**恰好等于**契约，防内部字段（如 `total` 之类实现细节）泄露 |
| `test_login_response_never_returns_password` | 登录响应不含 `password`，也不含口令明文 |
| `test_response_content_type_is_json` | `Content-Type` 为 `application/json` |
| `test_delete_returns_no_body` | 204 响应体为空 |
| `test_contract_helpers_catch_type_mismatch` | **给测试框架自己写用例**：验证 `check_schema` 能抓住类型不符、缺字段、枚举越界；尤其验证 `True` 不会被当成 `int` 放过（Python 里 `isinstance(True, int)` 是 `True`，这是常见的假绿来源） |

### 6. `test_readers.py` — 读者管理（13 条）

| 用例 | 覆盖点 | 设计方法 |
| --- | --- | --- |
| `test_reader_full_lifecycle` | 新增 → 出现在列表 → 删除（204）→ 再查已不在 | 场景法 |
| `test_created_reader_appears_in_list` | 写后读一致性 | 场景法 |
| `test_reader_accepts_each_declared_kind` | `student` / `teacher` / `public` 三种合法取值各一例 | 等价类 + 参数化 |
| `test_create_reader_rejects_invalid_payload` | 空名、全空白名、超长名、`vip`、`STUDENT`（大小写敏感）→ 422 | 等价类（参数化） |
| `test_delete_unknown_reader_returns_404` | 不存在 → 404 | 等价类 |
| `test_delete_reader_with_borrowings_returns_409` | 有借阅记录 → 409 | 业务规则 |

### 7. `test_regression.py` — 缺陷回归（10 条，全量挂 `regression`）

逐条对应 `docs/BUGS.md`。回归层的用例**只增不删**：一个坑踩过一次就够了。

| 用例 | 对应缺陷 | 断言要点 |
| --- | --- | --- |
| `test_bug001_exactly_at_limit_is_allowed` | BUG-001 | 刚好借满 5 本必须成功（防修过头） |
| `test_bug001_limit_plus_one_is_rejected` | BUG-001 | 第 6 本必须 400 |
| `test_bug002_page_zero_returns_422` | BUG-002 | page=0 不再是 500 |
| `test_bug002_invalid_pagination_never_returns_5xx` | BUG-002 | 非法分页只允许 4xx |
| `test_bug003_repeated_return_is_rejected` | BUG-003 | 重复归还 409 |
| `test_bug003_stock_not_inflated_by_repeated_return` | BUG-003 | 数据层：库存没被多加 |
| `test_bug004_blank_title_is_rejected` | BUG-004 | 空白/制表符/空串书名均 422 |
| `test_bug004_no_blank_titles_exist_in_database` | BUG-004 | 数据层：库里不存在空白书名 |
| `test_bug005_book_with_returned_history_can_be_deleted` | BUG-005 | 归还后能删除 |
| `test_bug005_borrowing_history_is_cascaded_not_orphaned` | BUG-005 | 数据层：没有悬挂的借阅记录 |

## 三、测试设计方法小结

| 方法 | 在本项目中的落点 |
| --- | --- |
| 等价类划分 | 登录凭据有效/无效、`kind` 合法/非法、关键字匹配/不匹配 |
| 边界值分析 | 书名 1/100/101 字符、stock 0/-1/1000/1001、size 1/100、page 0/1/2³¹-1、借阅上限 5/6 |
| 场景法 | 图书完整生命周期、借还闭环、读者生命周期 |
| 状态迁移 | 借阅记录 `borrowed → returned`、图书删除前后的外键状态 |
| 错误推测 | 重复归还、篡改令牌、并发借空库存、`True` 被当 `int` |
| 数据驱动 | `data/borrow_cases.yaml` 里的 5 条借书场景 |
| 性质断言 | "非法入参只能落 4xx"、"分页结果不重不漏"——比断言某个具体取值更难被绕过 |

## 四、夹具设计（面试常问的作用域问题）

`tests/conftest.py` 里的夹具按"代价高低"和"是否会被用例改脏"划作用域：

| 夹具 | 作用域 | 为什么是这个作用域 |
| --- | --- | --- |
| `api_server` | session | 启动 uvicorn 子进程约 1 秒，每个用例都重启会让 137 条用例跑几分钟；服务本身无状态，可以复用 |
| `base_url` / `db_path` | session | 从 `api_server` 派生，服务不重启就不该变 |
| `admin_token` | session | 令牌逻辑上是只读的，登录一次够用 |
| `api` / `anon` | function | `ApiClient` 持有 `requests.Session`（连接池）和调用历史，每个用例一个才能保证互不干扰、失败时能单独排查 |
| `factory` | function | **必须是函数级**：它造出来的数据要能被本用例独占，用完立刻清理。做成 session 级会让用例之间通过数据产生隐性依赖 |
| `new_book` / `new_reader` / `borrower` | function | 都是 `factory` 的语法糖，跟着 `factory` 走 |

**为什么把数据隔离做在"临时数据库 + 子进程"这一层，而不是每个用例清空数据库：**

1. 每个测试进程用 `tempfile.mkdtemp()` 建独立目录，注入 `LIBRARY_DB` 环境变量，
   被测服务启动时读到这个环境变量，用它自己的数据库文件；
2. 所以**测试永远不会碰到项目根目录下的 `library.db`**（这也是它被写进 `.gitignore` 的原因）；
3. 同时每个用例还通过 `factory.cleanup()` 归还并删除自己造的数据，
   保证 `pytest -n 4` 并行跑、或者用 `-p no:randomly` 乱序跑的时候结论一致。

`DataFactory.cleanup()` 的**清理顺序是关键**：先逐条归还未归还的借阅，再删图书，最后删读者。
顺序错了就会撞上业务约束（有未归还记录不能删图书/读者）留下垃圾数据。

## 五、断言层次

同一个业务动作，在这个项目里最多会从三个层次同时验证：

1. **接口层**：HTTP 状态码 + 响应体字段（`expect_status` / `expect_json`）；
2. **契约层**：响应结构是否符合声明的 `*_SCHEMA`（`expect_schema`）；
3. **数据层**：直连 SQLite 确认数据真的落盘（`stock_in_db` 等辅助函数）。

举一个具体的例子——重复归还（BUG-003）：

- 只断言接口层，会漏掉"状态码对了但库存被多加"的情况；
- 加上数据层断言，才能证明库存没有被凭空造出来。

再举一个——借阅上限（BUG-001）：

- 只断言"第 6 本被拒绝"，那么把 `>=` 误写成 `>` 之后测试**依然通过**（因为第 6 本还是被拒），
  但"刚好借满 5 本"这个合法场景被打死了。所以要成对断言上下边界。

## 六、断言失败时的可读性

自动化用例失败时最浪费时间的是"看到一个 `assert 500 == 204`，不知道请求是什么"。
本项目的断言封装统一带上了上下文：

`common/checks.py` 的 `brief(response)` 会把 `请求: METHOD URL`、`实际状态码`、`实际响应体`
一起拼进失败信息，每个断言还能带一句中文 `note`：

```
AssertionError: [归还后删除图书] 期望状态码 204，实际 500
请求: DELETE http://127.0.0.1:50213/api/books/20
实际状态码: 500
实际响应体: Internal Server Error
```

BUG-005 就是这样被定位的——不需要再手动复现一遍就能知道是哪个接口、哪本书。
