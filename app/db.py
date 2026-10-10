"""SQLite 数据访问层。

用标准库 sqlite3 而不是 ORM，是为了让「数据库断言」在测试里足够直白：
测试可以直接查库核对「借书后 stock 是否真的减 1」，而不必透过接口自己证明自己。

数据库文件路径由环境变量 LIBRARY_DB 指定，测试时为每个会话创建临时库，
从而保证用例之间以及用例与开发环境之间互不污染。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "library.db"
DB_PATH = Path(os.environ.get("LIBRARY_DB") or DEFAULT_DB_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    author     TEXT    NOT NULL DEFAULT '',
    stock      INTEGER NOT NULL DEFAULT 0,
    total      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS readers (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL,
    kind TEXT    NOT NULL DEFAULT 'student'
);

CREATE TABLE IF NOT EXISTS borrowings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    reader_id   INTEGER NOT NULL REFERENCES readers(id),
    borrowed_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    returned_at TEXT,
    status      TEXT    NOT NULL DEFAULT 'borrowed'
);

CREATE INDEX IF NOT EXISTS idx_borrowings_reader_status ON borrowings(reader_id, status);
CREATE INDEX IF NOT EXISTS idx_borrowings_book_status   ON borrowings(book_id, status);
"""

SEED_BOOKS = [
    ("软件测试的艺术", "Glenford Myers", 3, 3),
    ("Python 编程从入门到实践", "Eric Matthes", 5, 5),
    ("图解 HTTP", "上野宣", 2, 2),
    ("深入理解计算机系统", "Randal Bryant", 1, 1),
    ("代码整洁之道", "Robert Martin", 4, 4),
    ("计算机网络：自顶向下方法", "James Kurose", 2, 2),
    ("算法导论", "Thomas Cormen", 0, 0),
    ("数据库系统概念", "Abraham Silberschatz", 3, 3),
]

SEED_READERS = [
    ("张伟", "student"),
    ("李娜", "student"),
    ("王强", "teacher"),
    ("赵敏", "public"),
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session():
    """一次请求 = 一个连接 = 一个事务；正常结束提交，抛异常则回滚并关闭。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(seed: bool = True) -> None:
    """建表并按需灌入初始数据（幂等：只在 books 为空时灌数据）。"""
    with session() as conn:
        conn.executescript(SCHEMA)
        if not seed:
            return
        if conn.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"] == 0:
            conn.executemany(
                "INSERT INTO books (title, author, stock, total) VALUES (?, ?, ?, ?)",
                SEED_BOOKS,
            )
            conn.executemany(
                "INSERT INTO readers (name, kind) VALUES (?, ?)",
                SEED_READERS,
            )
