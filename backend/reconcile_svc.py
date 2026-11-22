"""对账互证：笼面可用 + 已拨付 + 已罚没 须等于笼面金额（同代口径）。

账户侧同源校验：账户冻结须等于活笼可用合计，且不得超过账户余额（超额
即说明出现幻影笼）。当日拒入登记单独列出，供五页同源核对。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect, now_iso

router = APIRouter(tags=["reconcile"])

LIVE_STATUSES = ("已冻结", "核验通过", "核验违约", "部分解笼", "部分罚没")


def _today() -> str:
    return now_iso()[:10]


def _active_rejections(conn, party_key: str, day: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id,party_key,amount,contract_no,status,day,actor,created_at
           FROM freeze_rejections
           WHERE party_key=? AND day=? AND status='占额'
           ORDER BY id DESC""",
        (party_key, day),
    ).fetchall()
    return [dict(r) for r in rows]


def _party_account_state(conn, party_key: str, day: str | None = None) -> dict:
    """账户与笼面同源事实：冻结 vs 活笼可用 vs 余额，含当日拒入占额。"""
    day = day or _today()
    acct = conn.execute(
        "SELECT balance, frozen FROM bond_accounts WHERE party_key=?",
        (party_key,),
    ).fetchone()
    live = conn.execute(
        """SELECT COALESCE(SUM(available),0) s FROM cages
           WHERE party_key=? AND status IN
           ('已冻结','核验通过','核验违约','部分解笼','部分罚没')""",
        (party_key,),
    ).fetchone()["s"]
    balance = float(acct["balance"]) if acct else 0.0
    frozen = float(acct["frozen"]) if acct else 0.0
    live_f = float(live)
    rejections = _active_rejections(conn, party_key, day)
    rejected_total = round(sum(float(r["amount"]) for r in rejections), 6)
    return {
        "balance": balance,
        "frozen": frozen,
        "live_cage_available": live_f,
        "frozen_matches_live": abs(frozen - live_f) < 1e-6,
        "over_limit": frozen > balance + 1e-6,
        "free_available": round(balance - frozen, 6),
        "active_rejections": rejections,
        "rejected_total": rejected_total,
        "day": day,
    }


def _reconcile_cage(conn, cage) -> dict:
    cage_id = cage["id"]
    gen = int(cage["generation"])
    amount = float(cage["amount"])
    available = float(cage["available"])
    released = conn.execute(
        """SELECT COALESCE(SUM(amount),0) s FROM releases
           WHERE cage_id=? AND status='已拨付' AND cage_generation=?""",
        (cage_id, gen),
    ).fetchone()["s"]
    forfeited = conn.execute(
        """SELECT COALESCE(SUM(amount),0) s FROM forfeits
           WHERE cage_id=? AND status='已罚没' AND cage_generation=?""",
        (cage_id, gen),
    ).fetchone()["s"]
    accounted = round(float(available) + float(released) + float(forfeited), 6)
    ok = abs(accounted - amount) < 1e-6
    # 账户侧：该当事人冻结额应覆盖所有仍冻结/核验中的笼可用
    party = cage["party_key"]
    state = _party_account_state(conn, party)
    return {
        "cage_id": cage_id,
        "cage_code": cage["cage_code"],
        "party_key": party,
        "contract_no": cage["contract_no"],
        "generation": gen,
        "status": cage["status"],
        "amount": amount,
        "available": available,
        "released": float(released),
        "forfeited": float(forfeited),
        "accounted": accounted,
        "balanced": ok and not state["over_limit"],
        "cage_balanced": ok,
        "gap": round(amount - accounted, 6),
        "account_balance": state["balance"],
        "account_frozen": state["frozen"],
        "live_cage_available": state["live_cage_available"],
        "account_over_limit": state["over_limit"],
        "rejected_total": state["rejected_total"],
        "active_rejection_count": len(state["active_rejections"]),
        "proof": {
            "formula": "available + released + forfeited == amount（同笼代）",
            "left": accounted,
            "right": amount,
        },
    }


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    day = _today()
    with connect() as conn:
        cages = conn.execute("SELECT * FROM cages ORDER BY id").fetchall()
        items = [_reconcile_cage(conn, c) for c in cages]
        bad = [i for i in items if not i["balanced"]]
        parties = conn.execute(
            "SELECT party_key FROM bond_accounts ORDER BY id"
        ).fetchall()
        states = []
        for p in parties:
            state = _party_account_state(conn, p["party_key"], day)
            state["party_key"] = p["party_key"]
            states.append(state)
        over = [s for s in states if s["over_limit"] or not s["frozen_matches_live"]]
        return {
            "items": items,
            "parties": states,
            "day": day,
            "summary": {
                "total": len(items),
                "balanced": len(items) - len(bad),
                "unbalanced": len(bad),
                "parties_over_limit": len(over),
                "over_limit_parties": len(over),
                "over_limit_keys": [s["party_key"] for s in over],
                "rejection_quota_parties": len(
                    [s for s in states if s["active_rejections"]]
                ),
            },
        }


@router.get("/api/reconcile/{cage_code}")
def reconcile_one(cage_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        cage = conn.execute(
            "SELECT * FROM cages WHERE cage_code=?", (cage_code,)
        ).fetchone()
        if not cage:
            raise HTTPException(404, "笼不存在")
        return _reconcile_cage(conn, cage)


@router.get("/api/reconcile-party/{party_key}")
def reconcile_party(party_key: str, authorization: AuthHeader = None):
    """账户页/对账页共用的当事人同源账实。"""
    require_user(authorization)
    with connect() as conn:
        acct = conn.execute(
            "SELECT * FROM bond_accounts WHERE party_key=?", (party_key,)
        ).fetchone()
        if not acct:
            raise HTTPException(404, "保证金户不存在")
        state = _party_account_state(conn, party_key)
        return {"party_key": party_key, "account": dict(acct), **state}


@router.get("/api/proof/{party_key}")
def party_proof(party_key: str, authorization: AuthHeader = None):
    """当事人级互证：账户冻结 vs 活笼可用合计。"""
    require_user(authorization)
    with connect() as conn:
        acct = conn.execute(
            "SELECT * FROM bond_accounts WHERE party_key=?", (party_key,)
        ).fetchone()
        if not acct:
            raise HTTPException(404, "保证金户不存在")
        live = conn.execute(
            """SELECT COALESCE(SUM(available),0) s FROM cages
               WHERE party_key=? AND status IN
               ('已冻结','核验通过','核验违约','部分解笼','部分罚没')""",
            (party_key,),
        ).fetchone()["s"]
        released = conn.execute(
            """SELECT COALESCE(SUM(r.amount),0) s FROM releases r
               JOIN cages c ON c.id=r.cage_id
               WHERE c.party_key=? AND r.status='已拨付'""",
            (party_key,),
        ).fetchone()["s"]
        forfeited = conn.execute(
            """SELECT COALESCE(SUM(f.amount),0) s FROM forfeits f
               JOIN cages c ON c.id=f.cage_id
               WHERE c.party_key=? AND f.status='已罚没'""",
            (party_key,),
        ).fetchone()["s"]
        state = _party_account_state(conn, party_key)
        return {
            "party_key": party_key,
            "account": dict(acct),
            "live_cage_available": float(live),
            "released_total": float(released),
            "forfeited_total": float(forfeited),
            "frozen_matches_live": state["frozen_matches_live"],
            "account_over_limit": state["over_limit"],
            "active_rejections": state["active_rejections"],
            "rejected_total": state["rejected_total"],
            "day": state["day"],
            "proof": {
                "formula": "account.frozen == sum(live cage available)",
                "left": state["frozen"],
                "right": float(live),
            },
        }
