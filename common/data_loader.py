"""测试数据加载（数据驱动）。

把「测试数据」从「测试逻辑」里拆出来放到 data/*.yaml：
  - 新增一组等价类 / 边界值用例时只改 YAML，不动代码
  - 数据本身可被评审（产品和开发能看懂用例表）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_yaml(filename: str) -> dict[str, Any]:
    path = DATA_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"测试数据文件不存在：{path}")
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_cases(filename: str, key: str) -> list[dict[str, Any]]:
    data = load_yaml(filename)
    if key not in data:
        raise KeyError(f"{filename} 中不存在用例集 {key!r}，可用的有：{sorted(data)}")
    cases = data[key]
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{filename} 的用例集 {key!r} 必须是非空列表")
    for case in cases:
        if "case_id" not in case:
            raise ValueError(f"{filename} 的用例集 {key!r} 中存在缺少 case_id 的用例：{case}")
    return cases
