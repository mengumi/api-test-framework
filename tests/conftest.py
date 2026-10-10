"""pytest 全局夹具。

夹具作用域的设计（这是接口测试面试的高频追问点，所以在这里写清理由）：

  api_server   session  —— 启动被测服务是重量级操作（拉进程 + 建库 + 灌数据），
                           整个测试会话只应做一次，绝不能每个用例都重启
  admin_token  session  —— 令牌是无状态的，登录一次即可长时间复用，
                           放进 function 作用域只是白白增加请求数和耗时
  api / anon   function —— 客户端持有可变状态（请求头里的令牌），必须每个用例一份，
                           否则一个用例改了鉴权状态会污染后面的用例
  factory      function —— 负责「造数据 + 用完清理」，保证用例可重复执行、
                           且不依赖用例的执行顺序
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests

from common.client import ApiClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ADMIN = {"username": "admin", "password": "admin123"}
SERVER_READY_TIMEOUT = 40


def _free_port() -> int:
    """向系统申请一个空闲端口，避免与本地已占用端口冲突。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def api_server() -> dict:
    """以子进程方式启动被测服务，并等待健康检查通过。

    每个测试会话使用独立的临时数据库（环境变量 LIBRARY_DB），
    这样测试数据与开发环境完全隔离，会话结束即删除。
    """
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    workdir = Path(tempfile.mkdtemp(prefix="library-api-test-"))
    log_path = workdir / "server.log"

    env = {
        **os.environ,
        "LIBRARY_DB": str(workdir / "library.db"),
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONUNBUFFERED": "1",
    }

    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1",
                "--port", str(port),
                "--log-level", "warning",
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

        try:
            deadline = time.time() + SERVER_READY_TIMEOUT
            while True:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"被测服务启动失败（退出码 {process.returncode}）：\n"
                        f"{log_path.read_text(encoding='utf-8', errors='replace')}"
                    )
                try:
                    if requests.get(f"{base_url}/api/health", timeout=1).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                if time.time() > deadline:
                    raise RuntimeError(
                        f"被测服务 {SERVER_READY_TIMEOUT}s 内未就绪：{base_url}\n"
                        f"{log_path.read_text(encoding='utf-8', errors='replace')}"
                    )
                time.sleep(0.2)

            yield {"base_url": base_url, "db_path": workdir / "library.db"}
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            shutil.rmtree(workdir, ignore_errors=True)


@pytest.fixture(scope="session")
def base_url(api_server: dict) -> str:
    return api_server["base_url"]


@pytest.fixture(scope="session")
def db_path(api_server: dict) -> Path:
    """被测服务的数据库文件，供用例直接查库做数据断言。"""
    return api_server["db_path"]


@pytest.fixture(scope="session")
def admin_token(base_url: str) -> str:
    response = requests.post(f"{base_url}/api/auth/login", json=ADMIN, timeout=5)
    assert response.status_code == 200, f"预置条件失败：管理员登录异常 → {response.status_code} {response.text}"
    return response.json()["access_token"]


@pytest.fixture
def api(base_url: str, admin_token: str) -> ApiClient:
    """已鉴权的客户端，覆盖绝大多数业务用例。"""
    client = ApiClient(base_url, token=admin_token)
    yield client
    client.close()


@pytest.fixture
def anon(base_url: str) -> ApiClient:
    """未鉴权的客户端，用于登录与鉴权失败场景。"""
    client = ApiClient(base_url)
    yield client
    client.close()


class DataFactory:
    """测试数据工厂：造出来的数据在用例结束后统一清理。

    这样用例既不用关心「上一轮跑完剩了什么」，也不依赖执行顺序，
    单独执行、乱序执行、并发执行（pytest-xdist）都能得到一致结果。
    """

    def __init__(self, api: ApiClient) -> None:
        self.api = api
        self._book_ids: list[int] = []
        self._reader_ids: list[int] = []

    def book(self, **overrides) -> dict:
        payload = {"title": "接口测试样本书", "author": "自动化用例", "stock": 3}
        payload.update(overrides)
        response = self.api.create_book(payload)
        assert response.status_code == 201, f"预置数据失败：新增图书 → {response.status_code} {response.text}"
        book = response.json()
        self._book_ids.append(book["id"])
        return book

    def reader(self, **overrides) -> dict:
        payload = {"name": "接口测试读者", "kind": "student"}
        payload.update(overrides)
        response = self.api.create_reader(payload)
        assert response.status_code == 201, f"预置数据失败：新增读者 → {response.status_code} {response.text}"
        reader = response.json()
        self._reader_ids.append(reader["id"])
        return reader

    def cleanup(self) -> None:
        # 顺序很重要：先把未归还的书还掉，才能删图书，最后才能删读者（外键 + 业务约束）
        for reader_id in self._reader_ids:
            response = self.api.reader_borrowings(reader_id, status="borrowed")
            if response.status_code != 200:
                continue
            for record in response.json()["items"]:
                self.api.return_book(record["id"])
        for book_id in self._book_ids:
            self.api.delete_book(book_id)
        for reader_id in self._reader_ids:
            self.api.delete_reader(reader_id)


@pytest.fixture
def factory(api: ApiClient) -> DataFactory:
    data_factory = DataFactory(api)
    yield data_factory
    data_factory.cleanup()


@pytest.fixture
def new_book(factory: DataFactory) -> dict:
    return factory.book()


@pytest.fixture
def new_reader(factory: DataFactory) -> dict:
    return factory.reader()


@pytest.fixture
def borrower(factory: DataFactory, api: ApiClient) -> dict:
    """一个专门的读者 + 「连续借 N 本书」的能力，用于借阅上限相关的用例。"""

    def borrow(count: int) -> list[dict]:
        records = []
        for index in range(1, count + 1):
            book = factory.book(title=f"上限测试图书-{index}", stock=1)
            response = api.borrow(book["id"], reader["id"])
            assert response.status_code == 201, f"预置数据失败：第 {index} 次借书 → {response.status_code} {response.text}"
            records.append(response.json())
        return records

    reader = factory.reader(name="借阅上限测试读者")
    return {"reader": reader, "borrow": borrow}
