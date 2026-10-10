"""变异验证：把每一条缺陷修复回退掉，确认对应的回归用例真的会变红。

「用例覆盖了这个缺陷」和「用例能抓住这个缺陷」是两回事。
这个脚本用变异验证（mutation check）的方式证明后者：
逐个把修复回退成有缺陷的写法 → 跑对应用例 → 必须失败 → 恢复源码。

用法：
    python tools/verify_regression.py
退出码 0 表示 5 条缺陷全部被对应用例抓住。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():  # 非 Windows 或未建虚拟环境时退回当前解释器
    PYTHON = Path(sys.executable)

RETURN_STATUS_CHECK = '''        if record["status"] == "returned":
            raise HTTPException(status_code=409, detail="该借阅记录已归还，请勿重复归还")
'''

BLANK_TITLE_VALIDATOR = '''    @field_validator("title")
    @classmethod
    def _reject_blank_title(cls, value: str) -> str:
        """长度校验通过但全是空白字符的书名同样属于非法输入。"""
        stripped = value.strip()
        if not stripped:
            raise ValueError("title 不能只由空白字符组成")
        return stripped

'''

MUTATIONS = [
    {
        "bug": "BUG-001",
        "file": "app/main.py",
        "old": ">= MAX_ACTIVE_BORROWINGS",
        "new": "> MAX_ACTIVE_BORROWINGS",
        "test": "tests/test_regression.py::test_bug001_limit_plus_one_is_rejected",
    },
    {
        "bug": "BUG-002",
        "file": "app/main.py",
        "old": 'default=1, ge=1, description="页码，从 1 开始"',
        "new": 'default=1, description="页码，从 1 开始"',
        "test": "tests/test_regression.py::test_bug002_page_zero_returns_422",
    },
    {
        "bug": "BUG-003",
        "file": "app/main.py",
        "old": RETURN_STATUS_CHECK,
        "new": "",
        "test": "tests/test_regression.py::test_bug003_repeated_return_is_rejected",
    },
    {
        "bug": "BUG-004",
        "file": "app/schemas.py",
        "old": BLANK_TITLE_VALIDATOR,
        "new": "",
        "test": "tests/test_regression.py::test_bug004_blank_title_is_rejected",
    },
    {
        "bug": "BUG-005",
        "file": "app/db.py",
        "old": "REFERENCES books(id) ON DELETE CASCADE",
        "new": "REFERENCES books(id)",
        "test": "tests/test_regression.py::test_bug005_book_with_returned_history_can_be_deleted",
    },
]


def run(nodeid: str) -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    completed = subprocess.run(
        [str(PYTHON), "-m", "pytest", nodeid, "-q", "--no-header", "-o", "addopts="],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    return completed.returncode


def main() -> int:
    failures = []
    for mutation in MUTATIONS:
        path = ROOT / mutation["file"]
        original = path.read_text(encoding="utf-8")
        assert mutation["old"] in original, f"{mutation['bug']}: 源码里找不到待回退的片段"

        path.write_text(original.replace(mutation["old"], mutation["new"], 1), encoding="utf-8")
        try:
            code = run(mutation["test"])
        finally:
            path.write_text(original, encoding="utf-8")

        caught = code != 0
        print(f"{mutation['bug']}  回退修复后用例退出码={code}  {'已抓住' if caught else '没抓住 <<< 问题'}")
        if not caught:
            failures.append(mutation["bug"])

    print()
    if failures:
        print(f"以下缺陷回退后用例仍然通过，说明覆盖无效：{failures}")
        return 1
    print(f"全部 {len(MUTATIONS)} 条缺陷在回退修复后都被对应用例抓住。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
