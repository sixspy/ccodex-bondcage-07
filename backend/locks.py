"""笼锁与争用登记（公共并发面）。

锁用独立连接 + BEGIN IMMEDIATE 原子抢占，立即落库：同一当事人同一时刻
只允许一个请求持有账户锁。持有者身份是「请求令牌」（用户名#随机），不是
用户名——同一账号在两个窗口提交也会互相争用。后到者只写 contention_log，
不写任何业务。
"""
from __future__ import annotations

import sqlite3
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db as db_module
from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"


def new_holder(user: str) -> str:
    """每次请求一个独立持有者令牌，避免同账号多窗口被当成同一持有者。"""
    return f"{user}#{uuid.uuid4().hex[:10]}"


def _lock_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_module.DB_PATH), timeout=0.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _log_contention(lock_key: str, holder: str, detail: str) -> None:
    """争用痕迹用独立连接立即提交，业务事务回滚也不会丢。"""
    actor = holder.split("#", 1)[0]
    with connect() as conn:
        conn.execute(
            """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (lock_key, actor, "争用", detail, now_iso()),
        )
        conn.commit()


def try_acquire(conn, lock_key: str, holder: str) -> bool:
    lconn = _lock_conn()
    try:
        try:
            lconn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            # 另一请求的业务事务在途：抢锁失败，只留争用痕
            _log_contention(lock_key, holder, "账户事务在途")
            return False
        row = lconn.execute(
            "SELECT holder FROM cage_locks WHERE lock_key=?", (lock_key,)
        ).fetchone()
        if row is not None:
            if row["holder"] == holder:
                lconn.execute("COMMIT")
                return True
            lconn.execute("ROLLBACK")
            _log_contention(lock_key, holder, f"持有者={row['holder']}")
            return False
        lconn.execute(
            "INSERT INTO cage_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
            (lock_key, holder, now_iso()),
        )
        lconn.execute("COMMIT")
        return True
    finally:
        lconn.close()


def release(conn, lock_key: str, holder: str) -> None:
    """释放锁：与业务写入同一连接、同一事务，避免锁未随业务一起落地。"""
    conn.execute(
        "DELETE FROM cage_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


def require_lock(conn, lock_key: str, holder: str) -> None:
    if not try_acquire(conn, lock_key, holder):
        raise HTTPException(409, "争用：后到只记争用，不写业务")


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cage_locks ORDER BY acquired_at DESC"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/contention")
def list_contention(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/locks/acquire")
def acquire_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    holder = new_holder(user)
    with connect() as conn:
        ok = try_acquire(conn, body.lock_key, holder)
        write_audit(
            conn,
            user,
            "lock_acquire",
            body.lock_key,
            {"ok": ok, "holder": holder},
        )
        conn.commit()
        if not ok:
            raise HTTPException(409, "争用：未能获得锁")
        return {"ok": True, "lock_key": body.lock_key, "holder": holder}


@router.post("/api/locks/release")
def release_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        conn.execute(
            "DELETE FROM cage_locks WHERE lock_key=? AND holder LIKE ?",
            (body.lock_key, user + "#%"),
        )
        write_audit(conn, user, "lock_release", body.lock_key, {})
        conn.commit()
        return {"ok": True}
