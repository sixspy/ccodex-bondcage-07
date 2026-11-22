"""入笼冻结：可用保证金划入笼内，写笼代。

超额入笼命运（乙路径，2026-11-22 当日锁死，见 docs/入笼超额命运说明.md）：
- 超额当次：正式笼零建、账户零改，只落拒入登记占住当日意图额度；审计记拒入。
- 同日再入：登记未撤销则继续拒绝，并记「超额偏航」；撤销登记后才放行。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import (
    FREEZE_OVER_LIMIT_FATE,
    FREEZE_OVER_LIMIT_FATE_LOCKED_ON,
    connect,
    now_iso,
    write_audit,
)
from locks import new_holder, release, require_lock
from models import CageFreezeIn

router = APIRouter(tags=["cages"])

REJECT_ACTIVE = "占额"
REJECT_REVOKED = "已撤销"


def _today() -> str:
    return now_iso()[:10]


def _fate_guard(conn, actor: str, detail: str) -> None:
    """命运说明若被偏离（配置不是乙路径），记超额偏航并拒绝写业务。"""
    if FREEZE_OVER_LIMIT_FATE != "乙" or FREEZE_OVER_LIMIT_FATE_LOCKED_ON != "2026-11-22":
        write_audit(
            conn,
            actor,
            "cage_freeze_deviation",
            "freeze_over_limit_fate",
            {
                "超额偏航": True,
                "detail": detail,
                "configured_fate": FREEZE_OVER_LIMIT_FATE,
                "locked_on": FREEZE_OVER_LIMIT_FATE_LOCKED_ON,
            },
        )
        raise HTTPException(409, "入笼超额命运说明被偏离，拒绝写业务")


def _next_cage_code(conn, party_key: str) -> str:
    n = conn.execute(
        "SELECT COUNT(*) c FROM cages WHERE party_key=?", (party_key,)
    ).fetchone()["c"]
    return f"CG-{party_key}-{n + 1:03d}"


def _rejection_row(row) -> dict:
    return dict(row)


@router.get("/api/cages")
def list_cages(authorization: AuthHeader = None, status: str | None = None):
    require_user(authorization)
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM cages WHERE status=? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM cages ORDER BY id DESC").fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/cages/freeze-rejections")
def list_freeze_rejections(
    authorization: AuthHeader = None,
    party_key: str | None = None,
    day: str | None = None,
    status: str | None = None,
):
    """拒入登记（乙路径）：默认只读当日占额登记，供入笼/账户/对账/审计页同源。"""
    require_user(authorization)
    where: list[str] = []
    args: list[object] = []
    if party_key:
        where.append("party_key=?")
        args.append(party_key)
    if day:
        where.append("day=?")
        args.append(day)
    if status:
        where.append("status=?")
        args.append(status)
    sql = "SELECT * FROM freeze_rejections"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        return {"items": [_rejection_row(r) for r in rows], "day": day or _today()}


@router.post("/api/cages/freeze-rejections/{rejection_id}/revoke")
def revoke_freeze_rejection(rejection_id: int, authorization: AuthHeader = None):
    """撤销当日拒入登记；撤销后才恢复同日再入资格。"""
    user = require_writer(authorization)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM freeze_rejections WHERE id=?", (rejection_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "拒入登记不存在")
        if row["status"] != REJECT_ACTIVE:
            raise HTTPException(409, "该登记已撤销")
        if row["day"] != _today():
            raise HTTPException(409, "只能撤销当日登记")
        lock_key = f"bond:{row['party_key']}"
        holder = new_holder(user)
        require_lock(conn, lock_key, holder)
        try:
            now = now_iso()
            conn.execute(
                """UPDATE freeze_rejections
                   SET status=?, revoked_at=?, revoked_by=?
                   WHERE id=?""",
                (REJECT_REVOKED, now, user, rejection_id),
            )
            write_audit(
                conn,
                user,
                "cage_freeze_rejection_revoke",
                row["party_key"],
                {"rejection_id": rejection_id, "amount": float(row["amount"])},
            )
            conn.commit()
            fresh = conn.execute(
                "SELECT * FROM freeze_rejections WHERE id=?", (rejection_id,)
            ).fetchone()
            return _rejection_row(fresh)
        finally:
            release(conn, lock_key, holder)
            conn.commit()


@router.get("/api/cages/{cage_id}")
def get_cage(cage_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        cage = conn.execute("SELECT * FROM cages WHERE id=?", (cage_id,)).fetchone()
        if not cage:
            raise HTTPException(404, "笼不存在")
        atts = conn.execute(
            "SELECT * FROM attestations WHERE cage_id=? ORDER BY id DESC",
            (cage_id,),
        ).fetchall()
        rels = conn.execute(
            "SELECT * FROM releases WHERE cage_id=? ORDER BY id DESC",
            (cage_id,),
        ).fetchall()
        fors = conn.execute(
            "SELECT * FROM forfeits WHERE cage_id=? ORDER BY id DESC",
            (cage_id,),
        ).fetchall()
        return {
            "cage": dict(cage),
            "attestations": [dict(a) for a in atts],
            "releases": [dict(r) for r in rels],
            "forfeits": [dict(f) for f in fors],
        }


@router.post("/api/cages/freeze")
def freeze_cage(body: CageFreezeIn, authorization: AuthHeader = None):
    """从账户可用余额划入新笼并冻结（乙路径超额拒入）。"""
    user = require_writer(authorization)
    lock_key = f"bond:{body.party_key}"
    holder = new_holder(user)
    today = _today()
    with connect() as conn:
        require_lock(conn, lock_key, holder)
        try:
            _fate_guard(conn, user, "freeze_cage")
            acct = conn.execute(
                "SELECT * FROM bond_accounts WHERE party_key=?",
                (body.party_key,),
            ).fetchone()
            if not acct:
                raise HTTPException(404, "保证金户不存在")
            if acct["status"] != "正常":
                raise HTTPException(409, "账户状态禁止入笼")

            # 当日拒入登记仍占额：同日再入须先撤销，否则继续拒绝并记偏航
            active = conn.execute(
                """SELECT * FROM freeze_rejections
                   WHERE party_key=? AND day=? AND status=?
                   ORDER BY id DESC LIMIT 1""",
                (body.party_key, today, REJECT_ACTIVE),
            ).fetchone()
            if active:
                write_audit(
                    conn,
                    user,
                    "cage_freeze_deviation",
                    body.party_key,
                    {
                        "超额偏航": True,
                        "reason": "当日拒入登记仍占额，未撤销即再入",
                        "rejection_id": active["id"],
                        "amount": float(body.amount),
                        "fate": FREEZE_OVER_LIMIT_FATE,
                    },
                )
                raise HTTPException(
                    409,
                    "当日拒入登记占额，须先撤销登记后再入笼",
                )

            amount = float(body.amount)
            free = float(acct["balance"]) - float(acct["frozen"])
            if amount > free + 1e-9:
                # 乙路径：落拒入登记占住当日意图额度；正式笼零建、账户零改
                cur = conn.execute(
                    """INSERT INTO freeze_rejections(party_key,amount,contract_no,
                       beneficiary,status,day,actor,created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        body.party_key,
                        amount,
                        body.contract_no,
                        body.beneficiary,
                        REJECT_ACTIVE,
                        today,
                        user,
                        now_iso(),
                    ),
                )
                write_audit(
                    conn,
                    user,
                    "cage_freeze_rejected",
                    body.party_key,
                    {
                        "rejection_id": cur.lastrowid,
                        "amount": amount,
                        "free": free,
                        "contract_no": body.contract_no,
                        "day": today,
                        "fate": FREEZE_OVER_LIMIT_FATE,
                        "note": "正式笼零建、账户零改；占当日意图额度",
                    },
                )
                raise HTTPException(409, "可用保证金不足，已登记拒入占额")

            code = body.cage_code or _next_cage_code(conn, body.party_key)
            dup = conn.execute(
                "SELECT id FROM cages WHERE cage_code=?", (code,)
            ).fetchone()
            if dup:
                raise HTTPException(409, "笼编码已存在")
            now = now_iso()
            cur = conn.execute(
                """INSERT INTO cages(cage_code,party_key,contract_no,amount,available,
                   generation,status,beneficiary,created_at,frozen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    code,
                    body.party_key,
                    body.contract_no,
                    amount,
                    amount,
                    1,
                    "已冻结",
                    body.beneficiary,
                    now,
                    now,
                ),
            )
            cage_id = cur.lastrowid
            conn.execute(
                "UPDATE bond_accounts SET frozen = frozen + ? WHERE party_key=?",
                (amount, body.party_key),
            )
            write_audit(
                conn,
                user,
                "cage_freeze",
                code,
                {
                    "cage_id": cage_id,
                    "amount": amount,
                    "contract_no": body.contract_no,
                    "beneficiary": body.beneficiary,
                    "generation": 1,
                },
            )
            conn.commit()
            row = conn.execute("SELECT * FROM cages WHERE id=?", (cage_id,)).fetchone()
            return dict(row)
        finally:
            release(conn, lock_key, holder)
            conn.commit()
