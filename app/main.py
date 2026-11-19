import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, ledger, money, security

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
DL_DIR = os.path.join(os.path.dirname(__file__), "..", "downloads")
SETTLE_INTERVAL = float(os.environ.get("MVULE_SETTLE_INTERVAL", "2"))


async def _settlement_worker():
    while True:
        try:
            with db.write_tx() as c:
                ledger.settle_due(c)
        except Exception as e:  # keep the worker alive
            print("settlement error:", e)
        await asyncio.sleep(SETTLE_INTERVAL)


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    with db.write_tx() as c:
        ledger.ensure_system_accounts(c)
    task = asyncio.create_task(_settlement_worker())
    yield
    task.cancel()


app = FastAPI(title="Mvule Pay Sandbox API", version="1.0.0", lifespan=lifespan,
              description="Sandbox payments API. No real money moves; all rails and banks are fictional.")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("MVULE_CORS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def sandbox_header(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Environment"] = "sandbox"
    return resp


@app.exception_handler(ledger.LedgerError)
async def _ledger_err(_req, exc: ledger.LedgerError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(money.MoneyError)
async def _money_err(_req, exc: money.MoneyError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ------------------------------------------------------------------ auth

_fails: dict[str, list[float]] = {}


def current_user(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    uid = security.read_token(token) if token else None
    if not uid:
        raise HTTPException(401, "Not signed in")
    with db.closing() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        raise HTTPException(401, "Not signed in")
    return u


class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    email: str = Field(max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str
    password: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


def _user_out(u):
    return {"id": u["id"], "full_name": u["full_name"], "email": u["email"], "phone": u["phone"], "created_at": u["created_at"]}


@app.post("/api/auth/register")
def register(body: RegisterIn):
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email):
        raise HTTPException(400, "Enter a valid email")
    phone = re.sub(r"[^\d+]", "", body.phone) if body.phone else None
    with db.write_tx() as c:
        if c.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE", (body.email,)).fetchone():
            raise HTTPException(409, "That email is already registered")
        if phone and c.execute("SELECT 1 FROM users WHERE phone=?", (phone,)).fetchone():
            raise HTTPException(409, "That phone is already registered")
        cur = c.execute("INSERT INTO users(email,phone,full_name,password_hash,created_at) VALUES(?,?,?,?,?)",
                        (body.email.strip(), phone, body.full_name.strip(), security.hash_password(body.password), ledger.iso()))
        ledger.open_wallets(c, cur.lastrowid)
        u = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"token": security.issue_token(u["id"]), "user": _user_out(u)}


@app.post("/api/auth/login")
def login(body: LoginIn):
    key = body.email.lower().strip()
    recent = [t for t in _fails.get(key, []) if time.time() - t < 300]
    if len(recent) >= 5:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    with db.closing() as c:
        u = c.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (key,)).fetchone()
    if not u or not security.verify_password(body.password, u["password_hash"]):
        _fails[key] = recent + [time.time()]
        raise HTTPException(401, "Wrong email or password")
    _fails.pop(key, None)
    return {"token": security.issue_token(u["id"]), "user": _user_out(u)}


@app.get("/api/me")
def me(u=Depends(current_user)):
    return _user_out(u)


@app.post("/api/me/password")
def change_password(body: PasswordIn, u=Depends(current_user)):
    if not security.verify_password(body.current_password, u["password_hash"]):
        raise HTTPException(400, "Current password is incorrect")
    with db.write_tx() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (security.hash_password(body.new_password), u["id"]))
    return {"ok": True}


# --------------------------------------------------------------- wallets

@app.get("/api/wallets")
def wallets(u=Depends(current_user)):
    with db.closing() as c:
        rows = c.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY id", (u["id"],)).fetchall()
        out = []
        for a in rows:
            bal = ledger.balance(c, a["id"])
            pending = c.execute("SELECT COALESCE(SUM(amount_minor),0) s FROM transactions WHERE from_account_id=? AND status='pending'",
                                (a["id"],)).fetchone()["s"]
            out.append({"currency": a["currency"], "symbol": money.CURRENCIES[a["currency"]]["symbol"],
                        "account_number": a["account_number"], "balance": money.from_minor(bal, a["currency"]),
                        "pending_out": money.from_minor(pending, a["currency"])})
    return out


@app.get("/api/meta")
def meta():
    return {"environment": "sandbox",
            "currencies": {k: {"symbol": v["symbol"], "decimals": v["exp"], "topup_max": v["topup_max"]} for k, v in money.CURRENCIES.items()},
            "rails": {k: {"name": v["name"], "settle_seconds": v["settle_seconds"], "needs_bank": v["needs_bank"], "banks": v["banks"]}
                      for k, v in money.RAILS.items()},
            "test_rules": ["Payouts whose amount ends in .13 (or 13 for UGX) are rejected and refunded."]}


# ---------------------------------------------------------- transactions

def _txn_out(c, t, viewer_id: int):
    mine = {r["id"] for r in c.execute("SELECT id FROM accounts WHERE user_id=?", (viewer_id,))}
    incoming = t["to_account_id"] in mine and t["from_account_id"] not in mine
    cp = json.loads(t["counterparty"]) if t["counterparty"] else None
    if t["type"] == "transfer" and incoming:
        s = c.execute("SELECT u.full_name, a.account_number FROM accounts a JOIN users u ON u.id=a.user_id WHERE a.id=?",
                      (t["from_account_id"],)).fetchone()
        cp = {"name": s["full_name"], "account_number": s["account_number"]}
    title = {"topup": "Test funds added", "payout": f"Payout · {money.RAILS.get(t['rail'], {}).get('name', '')}"}.get(t["type"])
    if t["type"] == "transfer":
        title = f"{'From' if incoming else 'To'} {cp['name']}"
    return {"ref": t["ref"], "type": t["type"], "status": t["status"], "direction": "in" if incoming or t["type"] == "topup" else "out",
            "title": title, "currency": t["currency"], "amount": money.from_minor(t["amount_minor"], t["currency"]),
            "rail": t["rail"], "counterparty": cp, "memo": t["memo"], "failure_reason": t["failure_reason"],
            "settle_at": t["settle_at"], "created_at": t["created_at"], "updated_at": t["updated_at"]}


@app.get("/api/transactions")
def transactions(limit: int = 50, currency: str | None = None, u=Depends(current_user)):
    limit = max(1, min(limit, 200))
    with db.closing() as c:
        q = ("SELECT t.* FROM transactions t WHERE (t.from_account_id IN (SELECT id FROM accounts WHERE user_id=?) "
             "OR t.to_account_id IN (SELECT id FROM accounts WHERE user_id=?))")
        args: list = [u["id"], u["id"]]
        if currency:
            q += " AND t.currency=?"
            args.append(money.check_currency(currency))
        rows = c.execute(q + " ORDER BY t.id DESC LIMIT ?", (*args, limit)).fetchall()
        return [_txn_out(c, t, u["id"]) for t in rows]


@app.get("/api/transactions/{ref}")
def transaction(ref: str, u=Depends(current_user)):
    with db.closing() as c:
        t = c.execute("SELECT * FROM transactions WHERE ref=?", (ref,)).fetchone()
        mine = {r["id"] for r in c.execute("SELECT id FROM accounts WHERE user_id=?", (u["id"],))}
        if not t or not ({t["from_account_id"], t["to_account_id"]} & mine):
            raise HTTPException(404, "Transaction not found")
        return _txn_out(c, t, u["id"])


class TopupIn(BaseModel):
    currency: str
    amount: str


class TransferIn(BaseModel):
    currency: str
    amount: str
    recipient: str
    memo: str | None = Field(default=None, max_length=140)


class PayoutIn(BaseModel):
    currency: str
    amount: str
    rail: str
    account_name: str = Field(max_length=80)
    account_number: str = Field(max_length=34)
    bank_code: str | None = None
    memo: str | None = Field(default=None, max_length=140)


def _amount(cur, amt):
    cur = money.check_currency(cur)
    return cur, money.to_minor(amt, cur)


@app.post("/api/topups")
def create_topup(body: TopupIn, u=Depends(current_user), idempotency_key: str | None = Header(default=None)):
    cur, minor = _amount(body.currency, body.amount)
    with db.write_tx() as c:
        t = ledger.topup(c, u["id"], cur, minor, idempotency_key)
        return _txn_out(c, t, u["id"])


@app.get("/api/recipients/lookup")
def lookup(q: str, u=Depends(current_user)):
    with db.closing() as c:
        r = ledger.find_recipient(c, q)
    if not r or r["id"] == u["id"]:
        raise HTTPException(404, "No other Mvule user found")
    parts = r["full_name"].split()
    return {"name": " ".join([parts[0]] + [p[0] + "." for p in parts[1:]])}


@app.post("/api/transfers")
def create_transfer(body: TransferIn, u=Depends(current_user), idempotency_key: str | None = Header(default=None)):
    cur, minor = _amount(body.currency, body.amount)
    with db.write_tx() as c:
        t = ledger.transfer(c, u["id"], cur, minor, body.recipient, body.memo, idempotency_key)
        return _txn_out(c, t, u["id"])


@app.post("/api/payouts")
def create_payout(body: PayoutIn, u=Depends(current_user), idempotency_key: str | None = Header(default=None)):
    cur, minor = _amount(body.currency, body.amount)
    details = {"account_name": body.account_name.strip(), "account_number": body.account_number.strip(), "bank_code": body.bank_code}
    with db.write_tx() as c:
        t = ledger.payout(c, u["id"], cur, minor, body.rail, details, body.memo, idempotency_key)
        return _txn_out(c, t, u["id"])


# ---------------------------------------------------------------- payees

class PayeeIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    kind: str
    details: dict


@app.get("/api/payees")
def payees(u=Depends(current_user)):
    with db.closing() as c:
        rows = c.execute("SELECT * FROM payees WHERE user_id=? ORDER BY label", (u["id"],)).fetchall()
    return [{"id": p["id"], "label": p["label"], "kind": p["kind"], "details": json.loads(p["details"])} for p in rows]


@app.post("/api/payees")
def add_payee(body: PayeeIn, u=Depends(current_user)):
    if body.kind not in ("internal", "bank", "mobile"):
        raise HTTPException(400, "Bad payee kind")
    with db.write_tx() as c:
        cur = c.execute("INSERT INTO payees(user_id,label,kind,details,created_at) VALUES(?,?,?,?,?)",
                        (u["id"], body.label, body.kind, json.dumps(body.details)[:1000], ledger.iso()))
    return {"id": cur.lastrowid}


@app.delete("/api/payees/{pid}")
def del_payee(pid: int, u=Depends(current_user)):
    with db.write_tx() as c:
        c.execute("DELETE FROM payees WHERE id=? AND user_id=?", (pid, u["id"]))
    return {"ok": True}


@app.get("/api/health")
def health():
    with db.closing() as c:
        return {"ok": True, "environment": "sandbox", "ledger_balanced": ledger.check_integrity(c)}


# -------------------------------------------------------------- downloads

if os.path.isdir(DL_DIR):
    app.mount("/downloads", StaticFiles(directory=DL_DIR), name="downloads")

LABELS = {"arm64-v8a": "Most phones (64-bit, 2017 and newer)", "armeabi-v7a": "Older 32-bit phones", "x86_64": "Emulators / Chromebooks"}


@app.get("/download", include_in_schema=False)
def download_page():
    from fastapi.responses import HTMLResponse
    files = sorted(f for f in os.listdir(DL_DIR) if f.endswith(".apk")) if os.path.isdir(DL_DIR) else []
    rows = ""
    for f in files:
        size = os.path.getsize(os.path.join(DL_DIR, f)) / 1_048_576
        label = next((v for k, v in LABELS.items() if k in f), "Android app")
        rows += (f'<a class="f" href="/downloads/{f}" download><b>{label}</b>'
                 f'<span>{f} · {size:.1f} MB</span></a>')
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mvule Pay Sandbox · Download</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono&family=DM+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<style>body{{margin:0;background:#F6F7F4;color:#0F1A14;font-family:'DM Sans',sans-serif}}
.b{{background:#FCF3E2;color:#B7791F;font:500 11px 'DM Mono',monospace;letter-spacing:.08em;text-align:center;padding:8px}}
main{{max-width:460px;margin:0 auto;padding:32px 18px}}h1{{letter-spacing:-.02em;margin:0 0 6px}}p{{color:#4B5A51;line-height:1.5}}
.f{{display:block;background:#fff;border:1px solid #E4E8E2;border-radius:14px;padding:16px;margin:10px 0;text-decoration:none;color:inherit}}
.f span{{display:block;color:#8A968E;font:12px 'DM Mono',monospace;margin-top:4px}}.f:first-of-type{{border-color:#1F6B45}}
a.w{{color:#1F6B45;font-weight:600}}</style></head><body>
<div class="b">SANDBOX · TEST FUNDS ONLY · NO REAL MONEY</div><main>
<h1>Mvule Pay Sandbox</h1><p>Install the Android app, or use the <a class="w" href="/">web app</a> in your browser. The app is already pointed at this server.</p>
{rows or '<p>No builds uploaded yet.</p>'}
<p style="font-size:13px">Android will ask you to allow installs from your browser. Not sure which file? Pick the first one.</p>
</main></body></html>"""
    return HTMLResponse(html)


# ------------------------------------------------------------- web client

if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))
