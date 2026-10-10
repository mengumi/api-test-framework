"""请求体 / 响应体的数据结构定义。

用 Pydantic 做入参校验：非法的请求会在进入业务逻辑之前被拦截并返回 422，
这本身就是「边界值测试」的靶子——测试用例需要证明校验规则确实在生效。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

TITLE_MAX = 100
AUTHOR_MAX = 50
NAME_MAX = 50
STOCK_MAX = 1000


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=64)


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX, description="书名，1~100 字符")
    author: str = Field(default="", max_length=AUTHOR_MAX, description="作者，最长 50 字符")
    stock: int = Field(default=1, ge=0, le=STOCK_MAX, description="可借库存，0~1000")

    @field_validator("title")
    @classmethod
    def _reject_blank_title(cls, value: str) -> str:
        """长度校验通过但全是空白字符的书名同样属于非法输入。"""
        stripped = value.strip()
        if not stripped:
            raise ValueError("title 不能只由空白字符组成")
        return stripped


class BookUpdate(BaseModel):
    """全部字段可选；只更新请求体中显式出现的字段。"""

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    author: str | None = Field(default=None, max_length=AUTHOR_MAX)
    stock: int | None = Field(default=None, ge=0, le=STOCK_MAX)


class ReaderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    kind: Literal["student", "teacher", "public"] = Field(
        default="student", description="读者类型，只允许 student / teacher / public"
    )

    @field_validator("name")
    @classmethod
    def _reject_blank_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name 不能只由空白字符组成")
        return stripped


class BorrowRequest(BaseModel):
    book_id: int = Field(ge=1)
    reader_id: int = Field(ge=1)


class ReturnRequest(BaseModel):
    borrowing_id: int = Field(ge=1)
