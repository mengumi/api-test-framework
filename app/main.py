"""被测服务（SUT）：图书借阅 API。

这是接口自动化测试的靶子，而不是本项目的重点交付物——它的存在是为了让测试
针对「真实 HTTP 服务 + 真实数据库」运行，而不是 mock 一堆返回值。

业务规则（同时是测试用例的设计依据）：
  1. 图书列表分页：page >= 1，1 <= size <= 100，越界返回 422
  2. 新增图书需鉴权，title 必填（1~100 字符，且不能全是空白），stock 取值 0~1000
  3. 借书需鉴权；图书不存在 404，读者不存在 404，库存为 0 返回 409
  4. 同一读者未归还图书上限 MAX_ACTIVE_BORROWINGS（5）本，超出返回 400
  5. 还书需鉴权；借阅记录不存在 404，重复归还返回 409
  6. 有未归还记录的图书 / 有借阅记录的读者不允许删除，返回 409
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query

from app import db
from app.auth import TTL_SECONDS, authenticate, create_token, current_user
from app.schemas import (
    BookCreate,
    BookUpdate,
    BorrowRequest,
    LoginRequest,
    ReaderCreate,
    ReturnRequest,
)

MAX_ACTIVE_BORROWINGS = 5


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="Library Borrowing API",
    description="图书借阅接口自动化测试的被测服务",
    version="1.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# 行 -> dict 的转换（必须在连接关闭前调用，避免拿到失效的 Row）
# --------------------------------------------------------------------------- #
def _book(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "stock": row["stock"],
        "total": row["total"],
    }


def _reader(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "kind": row["kind"]}


def _borrowing(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "reader_id": row["reader_id"],
        "status": row["status"],
        "borrowed_at": row["borrowed_at"],
        "returned_at": row["returned_at"],
    }


def _active_borrowings(conn: sqlite3.Connection, reader_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM borrowings WHERE reader_id = ? AND status = 'borrowed'",
        (reader_id,),
    ).fetchone()
    return int(row["c"])


# --------------------------------------------------------------------------- #
# 健康检查
# --------------------------------------------------------------------------- #
@app.get("/api/health", tags=["system"], summary="健康检查")
def health() -> dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# 鉴权
# --------------------------------------------------------------------------- #
@app.post("/api/auth/login", tags=["auth"], summary="登录获取令牌")
def login(payload: LoginRequest) -> dict[str, Any]:
    user = authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {
        "access_token": create_token(user["username"]),
        "token_type": "bearer",
        "expires_in": TTL_SECONDS,
        "username": user["username"],
        "role": user["role"],
    }


# --------------------------------------------------------------------------- #
# 图书
# --------------------------------------------------------------------------- #
@app.get("/api/books", tags=["books"], summary="图书列表（分页 + 书名模糊搜索）")
def list_books(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    size: int = Query(default=10, ge=1, le=100, description="每页条数，1~100"),
    keyword: str | None = Query(default=None, description="按书名模糊匹配"),
) -> dict[str, Any]:
    where, params = "", []
    if keyword:
        where = " WHERE title LIKE ?"
        params.append(f"%{keyword}%")
    offset = (page - 1) * size
    with db.session() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) AS c FROM books{where}", params).fetchone()["c"])
        rows = conn.execute(
            f"SELECT * FROM books{where} ORDER BY id ASC LIMIT ? OFFSET ?",
            [*params, size, offset],
        ).fetchall()
        items = [_book(r) for r in rows]
    return {"total": total, "page": page, "size": size, "items": items}


@app.get("/api/books/{book_id}", tags=["books"], summary="图书详情")
def get_book(book_id: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="图书不存在")
        return _book(row)


@app.post("/api/books", status_code=201, tags=["books"], summary="新增图书（需鉴权）")
def create_book(
    payload: BookCreate,
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    with db.session() as conn:
        cursor = conn.execute(
            "INSERT INTO books (title, author, stock, total) VALUES (?, ?, ?, ?)",
            (payload.title, payload.author, payload.stock, payload.stock),
        )
        row = conn.execute("SELECT * FROM books WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _book(row)


@app.put("/api/books/{book_id}", tags=["books"], summary="更新图书（需鉴权）")
def update_book(
    book_id: int,
    payload: BookUpdate,
    _: dict = Depends(current_user),
) -> dict[str, Any]:
    fields = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="请求体中没有可更新的字段")
    with db.session() as conn:
        if conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="图书不存在")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE books SET {assignments} WHERE id = ?",
            [*fields.values(), book_id],
        )
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return _book(row)


@app.delete("/api/books/{book_id}", status_code=204, tags=["books"], summary="删除图书（需鉴权）")
def delete_book(book_id: int, _: dict = Depends(current_user)) -> None:
    with db.session() as conn:
        if conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="图书不存在")
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM borrowings WHERE book_id = ? AND status = 'borrowed'",
            (book_id,),
        ).fetchone()["c"]
        if int(active) > 0:
            raise HTTPException(status_code=409, detail="该图书仍有未归还的借阅记录，不能删除")
        # 已归还的历史记录由外键的 ON DELETE CASCADE 一并清理，
        # 否则会撞上外键约束、接口抛 500（BUG-005）。
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))


# --------------------------------------------------------------------------- #
# 读者
# --------------------------------------------------------------------------- #
@app.get("/api/readers", tags=["readers"], summary="读者列表")
def list_readers() -> dict[str, Any]:
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM readers ORDER BY id ASC").fetchall()
        items = [_reader(r) for r in rows]
    return {"total": len(items), "items": items}


@app.post("/api/readers", status_code=201, tags=["readers"], summary="新增读者（需鉴权）")
def create_reader(payload: ReaderCreate, _: dict = Depends(current_user)) -> dict[str, Any]:
    with db.session() as conn:
        cursor = conn.execute(
            "INSERT INTO readers (name, kind) VALUES (?, ?)",
            (payload.name, payload.kind),
        )
        row = conn.execute("SELECT * FROM readers WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _reader(row)


@app.delete("/api/readers/{reader_id}", status_code=204, tags=["readers"], summary="删除读者（需鉴权）")
def delete_reader(reader_id: int, _: dict = Depends(current_user)) -> None:
    with db.session() as conn:
        if conn.execute("SELECT id FROM readers WHERE id = ?", (reader_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="读者不存在")
        used = conn.execute(
            "SELECT COUNT(*) AS c FROM borrowings WHERE reader_id = ?", (reader_id,)
        ).fetchone()["c"]
        if int(used) > 0:
            raise HTTPException(status_code=409, detail="该读者存在借阅记录，不能删除")
        conn.execute("DELETE FROM readers WHERE id = ?", (reader_id,))


# --------------------------------------------------------------------------- #
# 借阅
# --------------------------------------------------------------------------- #
@app.post("/api/borrow", status_code=201, tags=["borrow"], summary="借书（需鉴权）")
def borrow(payload: BorrowRequest, _: dict = Depends(current_user)) -> dict[str, Any]:
    with db.session() as conn:
        book = conn.execute("SELECT * FROM books WHERE id = ?", (payload.book_id,)).fetchone()
        if book is None:
            raise HTTPException(status_code=404, detail="图书不存在")
        if conn.execute("SELECT id FROM readers WHERE id = ?", (payload.reader_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="读者不存在")
        if int(book["stock"]) <= 0:
            raise HTTPException(status_code=409, detail="该图书已全部借出，库存不足")
        if _active_borrowings(conn, payload.reader_id) >= MAX_ACTIVE_BORROWINGS:
            raise HTTPException(
                status_code=400,
                detail=f"该读者未归还图书已达上限（{MAX_ACTIVE_BORROWINGS} 本）",
            )
        cursor = conn.execute(
            "INSERT INTO borrowings (book_id, reader_id, status) VALUES (?, ?, 'borrowed')",
            (payload.book_id, payload.reader_id),
        )
        conn.execute(
            "UPDATE books SET stock = stock - 1 WHERE id = ?", (payload.book_id,)
        )
        row = conn.execute("SELECT * FROM borrowings WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _borrowing(row)


@app.post("/api/return", tags=["borrow"], summary="还书（需鉴权）")
def return_book(payload: ReturnRequest, _: dict = Depends(current_user)) -> dict[str, Any]:
    with db.session() as conn:
        record = conn.execute(
            "SELECT * FROM borrowings WHERE id = ?", (payload.borrowing_id,)
        ).fetchone()
        if record is None:
            raise HTTPException(status_code=404, detail="借阅记录不存在")
        if record["status"] == "returned":
            raise HTTPException(status_code=409, detail="该借阅记录已归还，请勿重复归还")
        conn.execute(
            "UPDATE borrowings SET status = 'returned', returned_at = datetime('now', 'localtime') WHERE id = ?",
            (payload.borrowing_id,),
        )
        conn.execute("UPDATE books SET stock = stock + 1 WHERE id = ?", (record["book_id"],))
        row = conn.execute(
            "SELECT * FROM borrowings WHERE id = ?", (payload.borrowing_id,)
        ).fetchone()
        return _borrowing(row)


@app.get("/api/readers/{reader_id}/borrowings", tags=["borrow"], summary="读者的借阅记录")
def reader_borrowings(
    reader_id: int,
    status: str | None = Query(default=None, description="按状态过滤：borrowed / returned"),
) -> dict[str, Any]:
    with db.session() as conn:
        if conn.execute("SELECT id FROM readers WHERE id = ?", (reader_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="读者不存在")
        sql = (
            "SELECT b.*, k.title AS book_title, r.name AS reader_name "
            "FROM borrowings AS b "
            "JOIN books AS k ON k.id = b.book_id "
            "JOIN readers AS r ON r.id = b.reader_id "
            "WHERE b.reader_id = ?"
        )
        params: list[Any] = [reader_id]
        if status:
            sql += " AND b.status = ?"
            params.append(status)
        sql += " ORDER BY b.id ASC"
        rows = conn.execute(sql, params).fetchall()
        items = [
            {
                **_borrowing(r),
                "book_title": r["book_title"],
                "reader_name": r["reader_name"],
            }
            for r in rows
        ]
    return {"total": len(items), "items": items}
