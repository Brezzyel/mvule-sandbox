import os, tempfile, time, uuid
os.environ["MVULE_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["MVULE_JWT_SECRET"] = "test-secret-0123456789-abcdefghijklmnop"
os.environ["MVULE_SETTLE_INTERVAL"] = "0.2"
from fastapi.testclient import TestClient
from app.main import app
from app import db, ledger, money

money.RAILS["mockmomo"]["settle_seconds"] = 1
money.RAILS["mockbank"]["settle_seconds"] = 1


def reg(c, name, email, phone):
    r = c.post("/api/auth/register", json={"full_name": name, "email": email, "phone": phone, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def bal(c, h, cur):
    return next(w for w in c.get("/api/wallets", headers=h).json() if w["currency"] == cur)


def test_full_flow():
    with TestClient(app) as c:
        a = reg(c, "Alice Namu", "alice@example.com", "+256700000001")
        b = reg(c, "Bob Okello", "bob@example.com", "+256700000002")
        assert bal(c, a, "USD")["balance"] == "0.00"
        assert bal(c, a, "UGX")["balance"] == "0"
        # bad login
        assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": "nope"}).status_code == 401
        # insufficient funds
        r = c.post("/api/transfers", headers=a, json={"currency": "USD", "amount": "5", "recipient": "bob@example.com"})
        assert r.status_code == 402
        # top-up limits + idempotency
        assert c.post("/api/topups", headers=a, json={"currency": "USD", "amount": "5000"}).status_code == 400
        k = {**a, "Idempotency-Key": str(uuid.uuid4())}
        r1 = c.post("/api/topups", headers=k, json={"currency": "USD", "amount": "100.50"})
        r2 = c.post("/api/topups", headers=k, json={"currency": "USD", "amount": "100.50"})
        assert r1.json()["ref"] == r2.json()["ref"]
        assert bal(c, a, "USD")["balance"] == "100.50"
        assert c.post("/api/topups", headers=a, json={"currency": "USD", "amount": "1.001"}).status_code == 400
        # transfer by phone
        r = c.post("/api/transfers", headers=a, json={"currency": "USD", "amount": "40.25", "recipient": "+256700000002", "memo": "lunch"})
        assert r.status_code == 200 and r.json()["direction"] == "out"
        assert bal(c, b, "USD")["balance"] == "40.25"
        assert c.get("/api/transactions", headers=b).json()[0]["direction"] == "in"
        # payouts: one succeeds, one fails (ends .13) and refunds
        ok = c.post("/api/payouts", headers=a, json={"currency": "USD", "amount": "10", "rail": "mockmomo",
                                                     "account_name": "Alice", "account_number": "0700000001"}).json()
        bad = c.post("/api/payouts", headers=a, json={"currency": "USD", "amount": "0.13", "rail": "mockbank", "bank_code": "LKSD",
                                                      "account_name": "Alice", "account_number": "123"}).json()
        assert ok["status"] == "pending" and bad["status"] == "pending"
        assert bal(c, a, "USD")["balance"] == "50.12"
        time.sleep(2)
        assert c.get(f"/api/transactions/{ok['ref']}", headers=a).json()["status"] == "completed"
        f = c.get(f"/api/transactions/{bad['ref']}", headers=a).json()
        assert f["status"] == "failed" and f["failure_reason"]
        assert bal(c, a, "USD")["balance"] == "50.25"
        # privacy: bob can't read alice's payout
        assert c.get(f"/api/transactions/{ok['ref']}", headers=b).status_code == 404
        assert c.get("/api/health").json()["ledger_balanced"] is True
        # password change
        assert c.post("/api/me/password", headers=a, json={"current_password": "password123", "new_password": "newpass999"}).status_code == 200
        assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": "newpass999"}).status_code == 200
