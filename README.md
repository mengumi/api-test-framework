# 图书借阅 API · 接口自动化测试框架

![API Tests](https://github.com/mengumi/api-test-framework/actions/workflows/api-tests.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![Pytest](https://img.shields.io/badge/tested%20with-pytest-0A9EDC)

一个**开箱即跑**的接口自动化测试项目：自带被测服务（FastAPI + SQLite），
`pytest` 一套命令跑完 **137 条用例**，覆盖 13 个接口的正常流程、边界值、非法入参、鉴权、响应契约和数据一致性。

项目里还埋了一份真实的**缺陷记录**：5 个缺陷、5 条常驻回归用例，
以及一个**变异验证脚本**用来证明这些回归用例真的能抓住缺陷（而不是"跟着代码写出来的假绿"）。

```bash
git clone https://github.com/mengumi/api-test-framework.git
cd api-test-framework
python -m venv .venv && .venv/Scripts/activate     # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pytest
```

不需要提前准备数据库、不需要手动起服务、不需要配置任何环境变量——
用例会在临时目录里自己拉起被测服务，跑完自动回收。

---

## 项目亮点

| 亮点 | 说明 |
| --- | --- |
| **三层断言** | 同一个业务动作同时验证「HTTP 响应 + 响应契约 + 数据库真实数据」，避免"状态码对了但数据错了" |
| **四层用例分级** | `smoke` / `boundary` / `contract` / `regression` 四个 marker，CI 里可以先跑冒烟快速失败 |
| **完全隔离的测试数据** | 每个测试进程独立临时数据库 + 每个用例独立数据工厂，支持 `pytest -n 4` 并行和乱序执行 |
| **真实缺陷驱动** | 5 个缺陷，每个都有根因分析、修复 diff 和常驻回归用例（详见 [docs/BUGS.md](docs/BUGS.md)） |
| **变异验证** | `python tools/verify_regression.py` 把 5 条修复逐个回退，确认对应用例全部变红 |
| **数据驱动** | 借书场景写在 YAML 里，新增场景只改数据、不动测试代码 |
| **CI 矩阵** | GitHub Actions 在 Python 3.11 / 3.12 / 3.13 上跑全量用例，产出 HTML 报告作为构建产物 |
| **给测试框架自己也写了用例** | `test_contract_helpers_catch_type_mismatch` 验证断言封装本身能抓住类型错误（比如 `True` 被当成 `int`） |

---

## 目录结构

```
api-test-framework/
├── app/                      被测服务（SUT）
│   ├── main.py               13 个接口的实现，模块 docstring 里写了业务规则
│   ├── schemas.py            Pydantic 入参模型与字段校验
│   ├── auth.py               签名令牌（标准库 hmac，无需第三方 JWT 库）
│   └── db.py                 SQLite 建表 / 种子数据 / 会话管理
├── common/                   测试框架自身的公共层
│   ├── client.py             ApiClient：封装 requests，带调用历史
│   ├── checks.py             断言封装：状态码 / JSON / 契约校验
│   └── data_loader.py        YAML 测试数据加载
├── tests/
│   ├── conftest.py           夹具：临时库 + 子进程起服务 + 数据工厂
│   ├── test_auth.py          26 条 · 鉴权与令牌
│   ├── test_books_crud.py    24 条 · 图书增删改查与入参校验
│   ├── test_books_pagination.py  20 条 · 分页与搜索
│   ├── test_borrow_flow.py   25 条 · 借还主流程与库存一致性
│   ├── test_contract.py      19 条 · 响应契约
│   ├── test_readers.py       13 条 · 读者管理
│   └── test_regression.py    10 条 · 缺陷回归
├── data/
│   └── borrow_cases.yaml     数据驱动的借书场景
├── docs/
│   ├── BUGS.md               5 个缺陷的完整记录
│   └── TEST_CASES.md         用例清单与测试设计方法
├── tools/
│   └── verify_regression.py  变异验证：证明回归用例真的能抓住缺陷
├── .github/workflows/api-tests.yml
├── pytest.ini
└── requirements.txt
```

---

## 被测服务接口一览

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/health` | 否 | 健康检查 |
| POST | `/api/auth/login` | 否 | 登录换取令牌 |
| GET | `/api/books` | 否 | 图书列表（分页 + 关键字搜索） |
| GET | `/api/books/{id}` | 否 | 图书详情 |
| POST | `/api/books` | 是 | 新增图书 |
| PUT | `/api/books/{id}` | 是 | 修改图书（部分更新） |
| DELETE | `/api/books/{id}` | 是 | 删除图书 |
| GET | `/api/readers` | 否 | 读者列表 |
| POST | `/api/readers` | 是 | 新增读者 |
| DELETE | `/api/readers/{id}` | 是 | 删除读者 |
| POST | `/api/borrow` | 是 | 借书 |
| POST | `/api/return` | 是 | 还书 |
| GET | `/api/readers/{id}/borrowings` | 否 | 某读者的借阅记录 |

**业务规则**（写在 `app/main.py` 的模块 docstring 里，也是用例设计依据）：

1. 图书库存 `stock` 表示可借数量，借出 -1、归还 +1；
2. 同一读者未归还图书上限 **5 本**；
3. 有未归还记录的图书 / 有借阅记录的读者不能删除；
4. 同一条借阅记录不能被重复归还；
5. 书名、读者名不能只由空白字符组成；
6. 所有写操作需要 Bearer 令牌，读操作对匿名开放。

被测服务可以单独启动，自带交互式接口文档：

```bash
uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000/docs
```

演示账号：`admin / admin123`、`reader / reader123`。

---

## 运行测试

```bash
pytest                                    # 全量 137 条
pytest -m smoke                           # 只跑冒烟
pytest -m boundary                        # 边界与非法入参
pytest -m regression                      # 缺陷回归
pytest -m "not regression"                # 临时跳过回归层
pytest -n 4                               # 4 进程并行（约 6 秒）
pytest --html=reports/report.html --self-contained-html   # 生成离线 HTML 报告
```

> 并行执行是安全的：每个 worker 进程用自己的临时数据库和端口，
> 用例之间不共享数据。

验证回归用例的有效性：

```bash
python tools/verify_regression.py
# BUG-001  回退修复后用例退出码=1  已抓住
# ...
# 全部 5 条缺陷在回退修复后都被对应用例抓住。
```

---

## 关于"用例有没有用"

写自动化测试最容易自欺欺人的地方是：用例写了一大堆，但把被测代码改回有缺陷的版本，用例**照样通过**。
所以这个项目额外做了一件事——**变异验证**。

`tools/verify_regression.py` 里为每个缺陷记录了「有缺陷的写法」和「修复后的写法」。
脚本会把源码里的修复替换回缺陷写法，跑对应用例，要求必须失败，然后恢复源码：

```
BUG-001  回退修复后用例退出码=1  已抓住
BUG-002  回退修复后用例退出码=1  已抓住
BUG-003  回退修复后用例退出码=1  已抓住
BUG-004  回退修复后用例退出码=1  已抓住
BUG-005  回退修复后用例退出码=1  已抓住
```

这一步在 CI 里也会执行（只在 Python 3.13 上跑一次）。它证明的是：
这 5 条用例**确实能区分"有缺陷"和"没缺陷"两种代码**，而不是只描述了当前代码的行为。

---

## 文档

- [docs/BUGS.md](docs/BUGS.md) —— 5 个缺陷的现象、复现步骤、根因定位、修复方案与回归用例
- [docs/TEST_CASES.md](docs/TEST_CASES.md) —— 全部用例清单、测试设计方法、夹具作用域设计、断言层次

---

## 技术栈

`Python 3.11+` · `pytest` · `requests` · `pytest-html` · `pytest-xdist` · `PyYAML` · `FastAPI` · `SQLite` · `GitHub Actions`
