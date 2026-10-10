"""测试框架公共层：接口封装、断言工具、测试数据加载。"""

from common.checks import (
    ContractError,
    check_schema,
    expect_error,
    expect_json,
    expect_schema,
    expect_status,
    expect_status_in,
    json_body,
)
from common.client import ApiClient
from common.data_loader import load_cases, load_yaml

__all__ = [
    "ApiClient",
    "ContractError",
    "check_schema",
    "expect_error",
    "expect_json",
    "expect_schema",
    "expect_status",
    "expect_status_in",
    "json_body",
    "load_cases",
    "load_yaml",
]
