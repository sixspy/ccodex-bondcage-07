"""入笼超额事故夹具：先红后修。

三项失败测：
1. 余额不足当事人遭并发入笼时，两条请求都假成功 → 账户冻结被抬高、
   笼面出现超额幻影（第二只笼没有真实资金支撑）。
2. 乙路径：拒入登记占住当日意图额度后，同日余额补足直接再入仍成功，
   未先撤销登记（且未记超额偏航）。
3. 两人同时对同一当事人入笼：后到者应只留争用痕，不得建笼/改账户。

夹具跑在本机文件库（tmp_path 下的 sqlite 文件），入笼动作一律调用
POST /api/cages/freeze，以便拿到真实回执。
"""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

import cages_svc
from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "accident.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _open_bond(c: TestClient, h: dict, key: str, deposit: float) -> None:
    r = c.post(
        "/api/bonds",
        headers=h,
        json={"party_key": key, "party_name": "事故夹具-" + key, "initial_deposit": deposit},
    )
    assert r.status_code == 200, r.text


def _freeze_payload(party_key: str, amount: float, tag: str = "") -> dict:
    return {
        "party_key": party_key,
        "contract_no": "HT-ACC-" + party_key + ("-" + tag if tag else ""),
        "amount": amount,
        "beneficiary": "事故受益人",
    }


def _account(party_key: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM bond_accounts WHERE party_key=?", (party_key,)
        ).fetchone()
    return dict(row)


def _cage_count(party_key: str) -> int:
    with connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM cages WHERE party_key=?", (party_key,)
        ).fetchone()["c"]


def _contention(lock_key: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log WHERE lock_key=?", (lock_key,)
        ).fetchall()
    return [dict(r) for r in rows]


def _audit_actions() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM audit_trail ORDER BY id").fetchall()
    return [dict(r) for r in rows]


class _Seam:
    """在入笼写库前卡住请求，制造真实并发交错。

    交错点在请求线程池里执行，无法按线程名区分；用先进先出队列把每次
    到达映射到调用方声明的标签（A 先于 B 启动，故次序确定）。

    commit_lock=True 时在卡住前把已取的账户锁提交落地，模拟「同账号另一处
    已持锁」的真实窗口（旧锁按用户名判同持有者，于是被第二处直接放行）；
    默认不提交，模拟两名操作者真正同时进入的窗口（旧锁行未提交，双方都看
    不见对方，于是双双入笼）。
    """

    def __init__(self, monkeypatch, commit_lock: bool = False):
        self.entered: dict[str, threading.Event] = {}
        self.pending: list[str] = []
        self.lock = threading.Lock()
        self.go = threading.Event()
        self.commit_lock = commit_lock

        def fake_next(conn, party_key: str) -> str:
            if self.commit_lock:
                conn.commit()
            with self.lock:
                tag = self.pending.pop(0) if self.pending else "?"
            ev = self.entered.get(tag)
            if ev:
                ev.set()
            self.go.wait(10)
            return f"CG-SEAM-{tag}-{party_key}"

        monkeypatch.setattr(cages_svc, "_next_cage_code", fake_next)

    def event(self, tag: str) -> threading.Event:
        ev = threading.Event()
        self.entered[tag] = ev
        self.pending.append(tag)
        return ev


def _run_freeze(tag: str, out: dict, headers: dict, payload: dict) -> None:
    c = TestClient(app)
    try:
        r = c.post("/api/cages/freeze", headers=headers, json=payload)
        out[tag] = {"status": r.status_code, "body": r.json()}
    except Exception as exc:  # 事故夹具要看到真实异常，而不是测试直接崩
        out[tag] = {"status": -1, "body": {"error": repr(exc)}}


def test_1_over_limit_leak_phantom_cage(monkeypatch):
    """余额不足时并发入笼不得双双假成功。

    事故前置：同一账号两处同时提交，两笔金额之和超过账户余额。旧锁按
    用户名判持有者且锁行未随业务提交落地，两处互相看不见，于是双双读到
    同一可用额、双双假成功 → 账户冻结被抬高、笼面出现超额幻影。
    """
    c = TestClient(app)
    h = _auth(c)
    _open_bond(c, h, "P-LEAK", 100000)

    # 事故窗口：两处请求交错，先到者的锁行先落地；旧锁按用户名判同持有者，
    # 后到者据此被放行，于是两边都读到同一可用额、双双假成功。
    seam = _Seam(monkeypatch, commit_lock=True)
    ev_a = seam.event("A")
    ev_b = seam.event("B")
    out: dict = {}
    ta = threading.Thread(
        target=_run_freeze,
        name="A",
        args=("A", out, h, _freeze_payload("P-LEAK", 80000, "A")),
    )
    tb = threading.Thread(
        target=_run_freeze,
        name="B",
        args=("B", out, h, _freeze_payload("P-LEAK", 80000, "B")),
    )
    ta.start()
    assert ev_a.wait(3), "A 未进入事故交错点"
    tb.start()
    ev_b.wait(1.0)  # 修复后 B 会在账户写事务上被串行化，不必等满
    seam.go.set()
    ta.join(10)
    tb.join(10)

    ok = [r for r in out.values() if r["status"] == 200]
    # 事故断言：若两条都成功，账户冻结被人为抬高，笼面出现超额幻影
    assert len(ok) == 1, json.dumps(out, ensure_ascii=False)
    assert _cage_count("P-LEAK") == 1
    acct = _account("P-LEAK")
    assert acct["frozen"] == 80000
    assert acct["frozen"] <= acct["balance"] + 1e-6

    recon = c.get("/api/reconcile", headers=h).json()
    assert recon["summary"]["unbalanced"] == 0
    assert recon["summary"].get("over_limit_parties", 0) == 0
    proof = c.get("/api/proof/P-LEAK", headers=h).json()
    assert proof["frozen_matches_live"] is True
    assert proof["account"]["frozen"] <= proof["account"]["balance"] + 1e-6


def test_2_rejection_holds_daily_intent_quota(monkeypatch):
    """乙路径：拒入登记占额后，同日不撤销不得再入。"""
    c = TestClient(app)
    h = _auth(c)
    _open_bond(c, h, "P-REJ", 10000)

    # 第一次超额：拒入，正式笼零建、账户零改，但须落拒入登记
    r1 = c.post("/api/cages/freeze", headers=h, json=_freeze_payload("P-REJ", 50000))
    assert r1.status_code == 409, r1.text
    rejections = c.get(
        "/api/cages/freeze-rejections", headers=h, params={"party_key": "P-REJ"}
    ).json()["items"]
    active = [x for x in rejections if x["status"] == "占额"]
    assert len(active) == 1
    assert active[0]["amount"] == 50000
    assert _cage_count("P-REJ") == 0
    assert _account("P-REJ")["frozen"] == 0

    # 余额补足，但登记仍占住当日意图额度：直接再入必须继续拒绝
    assert (
        c.post(
            "/api/bonds/deposit",
            headers=h,
            json={"party_key": "P-REJ", "amount": 60000, "memo": "补足"},
        ).status_code
        == 200
    )
    r2 = c.post("/api/cages/freeze", headers=h, json=_freeze_payload("P-REJ", 50000))
    assert r2.status_code == 409, r2.text
    assert _cage_count("P-REJ") == 0
    assert _account("P-REJ")["frozen"] == 0
    flags = [
        a
        for a in _audit_actions()
        if a["action"] == "cage_freeze_deviation"
        and "超额偏航" in (a["payload"] or "")
    ]
    assert flags, "直接再入未记超额偏航"

    # 撤销登记后才允许再入
    rid = active[0]["id"]
    rv = c.post(f"/api/cages/freeze-rejections/{rid}/revoke", headers=h)
    assert rv.status_code == 200, rv.text
    r3 = c.post("/api/cages/freeze", headers=h, json=_freeze_payload("P-REJ", 50000))
    assert r3.status_code == 200, r3.text
    assert _cage_count("P-REJ") == 1
    acct = _account("P-REJ")
    assert acct["frozen"] == 50000
    assert acct["balance"] == 70000
    proof = c.get("/api/proof/P-REJ", headers=h).json()
    assert proof["frozen_matches_live"] is True
    recon = c.get("/api/reconcile", headers=h).json()
    assert recon["summary"]["unbalanced"] == 0
    assert recon["summary"].get("over_limit_parties", 0) == 0
    # 登记、撤销、拒入都在审计里
    actions = [a["action"] for a in _audit_actions()]
    assert "cage_freeze_rejected" in actions
    assert "cage_freeze_rejection_revoke" in actions


def test_3_two_actors_same_party_contention(monkeypatch):
    """两人同时对同一当事人入笼：后到只留争用痕。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    _open_bond(c, h_admin, "P-CONTEND", 300000)

    seam = _Seam(monkeypatch)
    ev_a = seam.event("A")
    ev_b = seam.event("B")
    out: dict = {}
    ta = threading.Thread(
        target=_run_freeze,
        name="A",
        args=("A", out, h_admin, _freeze_payload("P-CONTEND", 100000, "A")),
    )
    tb = threading.Thread(
        target=_run_freeze,
        name="B",
        args=("B", out, h_clerk, _freeze_payload("P-CONTEND", 100000, "B")),
    )
    ta.start()
    assert ev_a.wait(3), "A 未进入事故交错点"
    tb.start()
    ev_b.wait(1.0)
    seam.go.set()
    ta.join(10)
    tb.join(10)

    ok = [r for r in out.values() if r["status"] == 200]
    denied = [r for r in out.values() if r["status"] == 409]
    assert len(ok) == 1, json.dumps(out, ensure_ascii=False)
    assert len(denied) == 1, json.dumps(out, ensure_ascii=False)
    # 后到只留争用痕，正式笼与账户零增
    assert _cage_count("P-CONTEND") == 1
    assert _account("P-CONTEND")["frozen"] == 100000
    cont = _contention("bond:P-CONTEND")
    assert cont, "后到未留争用痕"
    assert any(x["actor"] == "clerk" for x in cont)
    # 后到者没有业务审计写入
    clerk_business = [
        a
        for a in _audit_actions()
        if a["actor"] == "clerk" and a["action"].startswith("cage_freeze")
    ]
    assert clerk_business == []
