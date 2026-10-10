"""接口调用封装（API Object 模式的简化版）。

分层的意义：URL、请求头、超时这些「接口细节」只在这里出现一次。
测试用例里只写业务动作（api.borrow(...)）和断言（expect_status(...)），
接口改路径时只需要改这一个文件，这正是接口测试框架该有的封装边界。
"""

from __future__ import annotations

from typing import Any

import requests

DEFAULT_TIMEOUT = 10


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.token: str | None = None
        # 保留调用历史，用例失败时可回溯「到底发了什么请求」
        self.history: list[requests.Response] = []
        if token:
            self.set_token(token)

    # ------------------------------------------------------------------ #
    # 基础设施
    # ------------------------------------------------------------------ #
    def set_token(self, token: str | None) -> None:
        self.token = token
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        else:
            self.session.headers.pop("Authorization", None)

    def anonymous(self) -> "ApiClient":
        """返回一个不带鉴权头的独立客户端，用于「未登录 / 令牌非法」场景。"""
        return ApiClient(self.base_url, timeout=self.timeout)

    def close(self) -> None:
        self.session.close()

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        response = self.session.request(method, f"{self.base_url}{path}", **kwargs)
        self.history.append(response)
        return response

    # ------------------------------------------------------------------ #
    # 业务接口
    # ------------------------------------------------------------------ #
    def health(self) -> requests.Response:
        return self.request("GET", "/api/health")

    def login(self, username: str, password: str) -> requests.Response:
        return self.request(
            "POST", "/api/auth/login", json={"username": username, "password": password}
        )

    def list_books(self, **params: Any) -> requests.Response:
        return self.request("GET", "/api/books", params=params)

    def get_book(self, book_id: int) -> requests.Response:
        return self.request("GET", f"/api/books/{book_id}")

    def create_book(self, payload: dict[str, Any]) -> requests.Response:
        return self.request("POST", "/api/books", json=payload)

    def update_book(self, book_id: int, payload: dict[str, Any]) -> requests.Response:
        return self.request("PUT", f"/api/books/{book_id}", json=payload)

    def delete_book(self, book_id: int) -> requests.Response:
        return self.request("DELETE", f"/api/books/{book_id}")

    def list_readers(self) -> requests.Response:
        return self.request("GET", "/api/readers")

    def create_reader(self, payload: dict[str, Any]) -> requests.Response:
        return self.request("POST", "/api/readers", json=payload)

    def delete_reader(self, reader_id: int) -> requests.Response:
        return self.request("DELETE", f"/api/readers/{reader_id}")

    def borrow(self, book_id: int, reader_id: int) -> requests.Response:
        return self.request(
            "POST", "/api/borrow", json={"book_id": book_id, "reader_id": reader_id}
        )

    def return_book(self, borrowing_id: int) -> requests.Response:
        return self.request("POST", "/api/return", json={"borrowing_id": borrowing_id})

    def reader_borrowings(self, reader_id: int, **params: Any) -> requests.Response:
        return self.request(
            "GET", f"/api/readers/{reader_id}/borrowings", params=params
        )
