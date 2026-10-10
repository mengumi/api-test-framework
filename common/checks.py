"""断言与契约校验工具。

一个合格的接口断言，除了「状态码对不对」，还应该覆盖：
  - 数据结构（字段有没有、类型对不对）—— 契约校验
  - 业务字段取值（status 只能是 borrowed / returned）
  - 错误响应是否给了可读的提示
并且失败信息必须能直接定位问题，否则等于把调试成本转嫁给下一个看报告的人。
"""

from __future__ import annotations

from typing import Any

import requests


class ContractError(AssertionError):
    """响应结构不符合接口契约。"""


def _names(types: tuple) -> str:
    return " | ".join(t.__name__ for t in types)


def brief(response: requests.Response, limit: int = 600) -> str:
    """把一次请求压缩成便于阅读的一行到几行，用于拼装失败信息。"""
    request = response.request
    body = response.text
    if len(body) > limit:
        body = f"{body[:limit]}…（共 {len(response.text)} 字符）"
    return (
        f"请求: {request.method} {request.url}\n"
        f"实际状态码: {response.status_code}\n"
        f"实际响应体: {body or '<空>'}"
    )


def expect_status(response: requests.Response, expected: int, note: str = "") -> requests.Response:
    prefix = f"[{note}] " if note else ""
    assert response.status_code == expected, (
        f"{prefix}期望状态码 {expected}，实际 {response.status_code}\n{brief(response)}"
    )
    return response


def expect_status_in(
    response: requests.Response, expected: tuple[int, ...], note: str = ""
) -> requests.Response:
    prefix = f"[{note}] " if note else ""
    assert response.status_code in expected, (
        f"{prefix}期望状态码属于 {expected}，实际 {response.status_code}\n{brief(response)}"
    )
    return response


def json_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise AssertionError(f"响应体不是合法 JSON\n{brief(response)}") from exc


def expect_json(
    response: requests.Response, expected_status: int = 200, note: str = ""
) -> Any:
    expect_status(response, expected_status, note)
    return json_body(response)


def check_schema(value: Any, spec: Any, path: str = "$") -> None:
    """递归校验数据结构，spec 支持的写法：

        {...}                      对象：列出必须存在的字段及其子契约
        [sub]                      数组：每个元素都必须满足 sub
        (str, type(None))          可空字段：取值类型属于其中任意一种即可
        ("borrowed", "returned")   枚举：取值必须属于其中之一
        int / str / bool / ...     基本类型校验（bool 不会被当成 int 放过）
    """
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            raise AssertionError(f"{path} 期望对象，实际为 {type(value).__name__}")
        for key, sub in spec.items():
            if key not in value:
                raise AssertionError(f"{path} 缺少字段 {key!r}，实际字段为 {sorted(value)}")
            check_schema(value[key], sub, f"{path}.{key}")
        return

    if isinstance(spec, list):
        if not isinstance(value, list):
            raise AssertionError(f"{path} 期望数组，实际为 {type(value).__name__}")
        if not spec:
            return
        for index, item in enumerate(value):
            check_schema(item, spec[0], f"{path}[{index}]")
        return

    if isinstance(spec, tuple):
        # 元组里全是类型 -> 表示「可空 / 多类型」；否则表示枚举候选值
        if spec and all(isinstance(item, type) for item in spec):
            if isinstance(value, bool) and bool not in spec:
                raise AssertionError(f"{path} 期望类型属于 {_names(spec)}，实际为 bool")
            if not isinstance(value, spec):
                raise AssertionError(
                    f"{path} 期望类型属于 {_names(spec)}，实际为 {type(value).__name__}（值 {value!r}）"
                )
            return
        if value not in spec:
            raise AssertionError(f"{path} 期望取值属于 {spec}，实际为 {value!r}")
        return

    if spec is int:
        # 注意 bool 是 int 的子类，必须显式排除，否则 True 会被当成合法整数
        if isinstance(value, bool) or not isinstance(value, int):
            raise AssertionError(f"{path} 期望 int，实际为 {type(value).__name__}（值 {value!r}）")
        return

    if not isinstance(value, spec):
        raise AssertionError(
            f"{path} 期望 {spec.__name__}，实际为 {type(value).__name__}（值 {value!r}）"
        )


def expect_schema(
    response: requests.Response,
    spec: Any,
    expected_status: int = 200,
    note: str = "",
) -> Any:
    """先校验状态码，再按 spec 校验响应结构，返回解析后的 JSON。"""
    data = expect_json(response, expected_status, note)
    try:
        check_schema(data, spec)
    except AssertionError as exc:
        prefix = f"[{note}] " if note else ""
        raise ContractError(f"{prefix}响应契约校验失败：{exc}\n{brief(response)}") from exc
    return data


def expect_error(
    response: requests.Response, expected_status: int, note: str = ""
) -> Any:
    """校验错误响应：状态码正确 + 带有非空 detail（错误提示质量也纳入断言）。"""
    data = expect_json(response, expected_status, note)
    if isinstance(data, dict):
        detail = data.get("detail")
        prefix = f"[{note}] " if note else ""
        assert detail, f"{prefix}错误响应缺少可读的 detail 字段\n{brief(response)}"
    return data
