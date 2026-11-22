import os
import tempfile
import uuid

os.environ["MVULE_DB"] = os.path.join(tempfile.mkdtemp(), "live.db")
os.environ.update({
    "MVULE_JWT_SECRET": "test-secret-0123456789-abcdefghijklmnop",
    "MVULE_LIVE": "1", "MVULE_PERSISTENT_DISK": "1",
    "FLW_SECRET_KEY": "FLWSECK_TEST-x", "FLW_SECRET_HASH": "hook-secret",
    "LIVE_OWNER_EMAILS": "owner@demo.test",
    "LIVE_ALLOWED_NUMBERS": "0772000111,+256 701 000 222",
    "LIVE_MAX_TXN": "50000", "LIVE_DAILY_MAX": "120000",
    "MVULE_SETTLE_INTERVAL": "100",  # keep background worker out of the way
})

import pytest
from fastapi.testclient import TestClient

from app import db, ledger, live
from app.main import app


class FakeFlw:
    """Stands in for Flutterwave. `charges`/`transfers` hold what the 'phone' did."""
    def __init__(self):
        self.charges, self.transfers, self.n = {}, {}, 100

    def charge(self, *, msisdn, network, amount, email, name, tx_ref):
        if msisdn.endswith("999"):
            raise live.FlutterwaveError("Invalid phone number")
        self.charges[tx_ref] = {"status": "pending", "amount": amount, "currency": "UGX", "id": 1, "flw_ref": "F1"}
        return {"status": "success", "meta": {"authorization": {"mode": "redirect", "redirect": "https://pay.example/x"}}}

    def verify_charge(self, tx_ref):
        if tx_ref not in self.charges:
            raise live.FlutterwaveError("No transaction was found for this id")
        return {"data": self.charges[tx_ref]}

    def transfer(self, *, msisdn, amount, name, reference, narration, bank):
        self.n += 1
        self.transfers[str(self.n)] = {"status": "NEW", "ref": reference}
        return {"data": {"id": self.n}}

    def get_transfer(self, i):
        return {"data": self.transfers[i]}

    def balance(self):
        return {"data": {"available_balance": 0}}


@pytest.fixture()
def env():
    fake = FakeFlw()
    live.provider = fake
    with TestClient(app) as c:
        def reg(email, phone):
            r = c.post("/api/auth/register", json={"full_name": "Brian Test", "email": email, "phone": phone, "password": "password123"})
            return {"Authorization": "Bearer " + r.json()["token"]}
        yield c, fake, reg


def bal(c, h):
    return c.get("/api/live/status", headers=h).json()["balance"]


def test_live_flow(env):
    c, fake, reg = env
    owner = reg("owner@demo.test", "+256700000010")
    other = reg("someone@demo.test", "+256700000011")

    # non-owners never see live mode
    assert c.get("/api/live/status", headers=other).json() == {"owner": False}
    assert c.post("/api/live/collections", headers=other, json={"amount": "1000", "phone": "0772000111"}).status_code == 403
    st = c.get("/api/live/status", headers=owner).json()
    assert st["ready"] and st["balance"] == "0" and len(st["numbers"]) == 2 and st["numbers"][1]["network"] == "AIRTEL"

    # whitelist + caps
    assert c.post("/api/live/collections", headers=owner, json={"amount": "1000", "phone": "0772555555"}).status_code == 400
    assert c.post("/api/live/collections", headers=owner, json={"amount": "60000", "phone": "0772000111"}).status_code == 400
    assert c.post("/api/live/collections", headers=owner, json={"amount": "100", "phone": "0772000111"}).status_code == 400

    # deposit: pending until Flutterwave says successful
    k = {**owner, "Idempotency-Key": str(uuid.uuid4())}
    r = c.post("/api/live/collections", headers=k, json={"amount": "20000", "phone": "0772000111"}).json()
    ref = r["transaction"]["ref"]
    assert r["transaction"]["status"] == "pending" and r["authorization"]["redirect"]
    assert c.post("/api/live/collections", headers=k, json={"amount": "20000", "phone": "0772000111"}).json()["transaction"]["ref"] == ref
    assert bal(c, owner) == "0"

    # webhook with wrong signature is refused; payload alone never credits
    assert c.post("/api/webhooks/flutterwave", json={"data": {"tx_ref": ref}}, headers={"verif-hash": "nope"}).status_code == 401
    c.post("/api/webhooks/flutterwave", json={"event": "charge.completed", "data": {"tx_ref": ref, "status": "successful"}},
           headers={"verif-hash": "hook-secret"})
    assert bal(c, owner) == "0"  # Flutterwave API still says pending

    fake.charges[ref]["status"] = "successful"
    for _ in range(2):  # duplicate webhooks must not double-credit
        c.post("/api/webhooks/flutterwave", json={"data": {"tx_ref": ref}}, headers={"verif-hash": "hook-secret"})
    assert bal(c, owner) == "20000"
    assert c.get(f"/api/transactions/{ref}", headers=owner).json()["status"] == "completed"

    # declined deposit
    r2 = c.post("/api/live/collections", headers=owner, json={"amount": "5000", "phone": "0701000222"}).json()["transaction"]["ref"]
    fake.charges[r2]["status"] = "failed"
    assert live.reconcile(r2) == "failed" and bal(c, owner) == "20000"

    # live funds can't go to other users
    assert c.post("/api/transfers", headers=owner, json={"currency": "UGX_LIVE", "amount": "100", "recipient": "someone@demo.test"}).status_code == 400

    # payout success
    p = c.post("/api/live/payouts", headers=owner, json={"amount": "7000", "phone": "0701000222"}).json()["transaction"]
    assert p["status"] == "pending" and bal(c, owner) == "13000"
    tid = next(k for k, v in fake.transfers.items() if v["ref"] == p["ref"])
    fake.transfers[tid]["status"] = "SUCCESSFUL"
    assert live.reconcile(p["ref"]) == "completed" and bal(c, owner) == "13000"

    # payout failure refunds
    p2 = c.post("/api/live/payouts", headers=owner, json={"amount": "3000", "phone": "0772000111"}).json()["transaction"]
    assert bal(c, owner) == "10000"
    tid2 = next(k for k, v in fake.transfers.items() if v["ref"] == p2["ref"])
    fake.transfers[tid2]["status"] = "FAILED"
    assert live.reconcile(p2["ref"]) == "failed" and bal(c, owner) == "13000"

    # overdraw blocked; 24h cap (20000+5000? failed excluded -> 20000+7000+3000(failed excl)=27000 used)
    assert c.post("/api/live/payouts", headers=owner, json={"amount": "14000", "phone": "0772000111"}).status_code == 402
    rec = c.get("/api/live/reconciliation", headers=owner).json()
    assert rec["user_live_balances"] == 13000 and rec["completed"] == {"topup": 20000, "payout": 7000}
    assert c.get("/api/health").json()["ledger_balanced"] is True

    # sandbox wallets untouched and live wallet absent for non-owner
    assert all(not w["live"] for w in c.get("/api/wallets", headers=other).json())


def test_daily_cap(env):
    c, fake, reg = env
    h = c.post("/api/auth/login", json={"email": "owner@demo.test", "password": "password123"})
    if h.status_code != 200:
        h = c.post("/api/auth/register", json={"full_name": "B", "email": "owner@demo.test", "password": "password123"})
    owner = {"Authorization": "Bearer " + h.json()["token"]}
    used = c.get("/api/live/status", headers=owner).json()["used_24h"]
    left = 120000 - used
    ok = c.post("/api/live/collections", headers=owner, json={"amount": str(min(left, 50000)), "phone": "0772000111"})
    assert ok.status_code == 200
    while True:
        used = c.get("/api/live/status", headers=owner).json()["used_24h"]
        if 120000 - used < 50000:
            break
        c.post("/api/live/collections", headers=owner, json={"amount": "50000", "phone": "0772000111"})
    r = c.post("/api/live/collections", headers=owner, json={"amount": "50000", "phone": "0772000111"})
    assert r.status_code == 400 and "24-hour" in r.json()["detail"]


def test_off_by_default(env, monkeypatch):
    c, fake, reg = env
    monkeypatch.setenv("MVULE_PERSISTENT_DISK", "0")
    h = c.post("/api/auth/login", json={"email": "owner@demo.test", "password": "password123"})
    owner = {"Authorization": "Bearer " + h.json()["token"]}
    st = c.get("/api/live/status", headers=owner).json()
    assert st["ready"] is False and "persistent" in st["reason"]
    assert c.post("/api/live/collections", headers=owner, json={"amount": "1000", "phone": "0772000111"}).status_code == 503
