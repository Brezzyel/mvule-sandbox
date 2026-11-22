"""Owner-only live mode: real UGX via Flutterwave (MTN + Airtel Uganda).

Safety rails, all enforced server-side:
  * OFF unless MVULE_LIVE=1, Flutterwave keys are set, and MVULE_PERSISTENT_DISK=1.
  * Only emails in LIVE_OWNER_EMAILS get a live wallet.
  * Money can only come from / go to numbers in LIVE_ALLOWED_NUMBERS.
  * Per-transaction and rolling-24h caps.
  * Live funds can't be sent to other users.
  * Provider webhooks are never trusted on their own: every status is re-checked
    against the Flutterwave API before the ledger moves.
"""
import json
import os
import re
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from . import db, ledger, money

LIVE = "UGX_LIVE"
RAIL = "flutterwave"
PENDING_TIMEOUT_MIN = 30


# ------------------------------------------------------------------ config
def env(k, d=""):
    return os.environ.get(k, d).strip()


def cfg():
    return {
        "on": env("MVULE_LIVE") == "1",
        "disk": env("MVULE_PERSISTENT_DISK") == "1",
        "secret": env("FLW_SECRET_KEY"),
        "hash": env("FLW_SECRET_HASH"),
        "owners": {e.strip().lower() for e in env("LIVE_OWNER_EMAILS").split(",") if e.strip()},
        "numbers": [n for n in (normalize_msisdn(x) for x in env("LIVE_ALLOWED_NUMBERS").split(",")) if n],
        "min": int(env("LIVE_MIN_TXN", "500")),
        "max": int(env("LIVE_MAX_TXN", "50000")),
        "daily": int(env("LIVE_DAILY_MAX", "200000")),
        "payout_bank": env("FLW_UG_PAYOUT_BANK", "MPS"),
    }


def not_ready_reason(c=None) -> str | None:
    c = c or cfg()
    if not c["on"]:
        return "Live mode is switched off (MVULE_LIVE is not 1)."
    if not c["secret"]:
        return "Flutterwave secret key is not set (FLW_SECRET_KEY)."
    if not c["hash"]:
        return "Webhook secret hash is not set (FLW_SECRET_HASH)."
    if not c["disk"]:
        return "Server storage isn't persistent yet (set MVULE_PERSISTENT_DISK=1 after adding a Render disk)."
    if not c["numbers"]:
        return "No whitelisted phone numbers (LIVE_ALLOWED_NUMBERS)."
    return None


def is_owner(user) -> bool:
    return (user["email"] or "").lower() in cfg()["owners"]


# ------------------------------------------------------------------ phones
MTN_PREFIXES = ("77", "78", "76", "39")
AIRTEL_PREFIXES = ("70", "75", "74", "20")


def normalize_msisdn(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("0") and len(d) == 10:
        d = "256" + d[1:]
    elif len(d) == 9:
        d = "256" + d
    return d if re.fullmatch(r"256\d{9}", d) else None


def network_of(msisdn: str) -> str | None:
    p = msisdn[3:5]
    return "MTN" if p in MTN_PREFIXES else "AIRTEL" if p in AIRTEL_PREFIXES else None


# --------------------------------------------------------------- provider
class FlutterwaveError(Exception):
    pass


class Flutterwave:
    def __init__(self):
        self.base = env("FLW_BASE_URL", "https://api.flutterwave.com/v3").rstrip("/")

    def _req(self, method, path, **kw):
        h = {"Authorization": f"Bearer {env('FLW_SECRET_KEY')}", "Content-Type": "application/json"}
        try:
            r = httpx.request(method, self.base + path, headers=h, timeout=25, **kw)
        except httpx.HTTPError as e:
            raise FlutterwaveError(f"Can't reach Flutterwave: {e.__class__.__name__}")
        try:
            data = r.json()
        except ValueError:
            raise FlutterwaveError(f"Flutterwave returned HTTP {r.status_code}")
        if r.status_code >= 400 or data.get("status") == "error":
            raise FlutterwaveError(data.get("message") or f"Flutterwave HTTP {r.status_code}")
        return data

    def charge(self, *, msisdn, network, amount, email, name, tx_ref):
        return self._req("POST", "/charges?type=mobile_money_uganda", json={
            "phone_number": msisdn, "network": network, "amount": amount, "currency": "UGX",
            "email": email, "fullname": name, "tx_ref": tx_ref})

    def verify_charge(self, tx_ref):
        return self._req("GET", "/transactions/verify_by_reference", params={"tx_ref": tx_ref})

    def transfer(self, *, msisdn, amount, name, reference, narration, bank):
        return self._req("POST", "/transfers", json={
            "account_bank": bank, "account_number": msisdn, "amount": amount, "currency": "UGX",
            "beneficiary_name": name, "reference": reference, "narration": narration[:100]})

    def get_transfer(self, transfer_id):
        return self._req("GET", f"/transfers/{transfer_id}")

    def balance(self):
        return self._req("GET", "/balances/UGX")


provider = Flutterwave()  # tests swap this out


# ---------------------------------------------------------------- helpers
def live_wallet(c, user_id):
    ledger.open_wallets(c, user_id, [LIVE])
    return ledger.wallet(c, user_id, LIVE)


def used_last_24h(c, user_id) -> int:
    since = ledger.iso(ledger.now() - timedelta(hours=24))
    return c.execute(
        "SELECT COALESCE(SUM(amount_minor),0) s FROM transactions WHERE user_id=? AND currency=? "
        "AND status!='failed' AND created_at>=?", (user_id, LIVE, since)).fetchone()["s"]


def _check(c, conf, user, amount, msisdn_raw):
    if (r := not_ready_reason(conf)):
        raise HTTPException(503, r)
    if not is_owner(user):
        raise HTTPException(403, "Live mode is limited to the account owner")
    msisdn = normalize_msisdn(msisdn_raw)
    if not msisdn or msisdn not in conf["numbers"]:
        raise HTTPException(400, "That number isn't on the live whitelist")
    if not (conf["min"] <= amount <= conf["max"]):
        raise HTTPException(400, f"Live amounts must be between UGX {conf['min']:,} and UGX {conf['max']:,}")
    if used_last_24h(c, user["id"]) + amount > conf["daily"]:
        raise HTTPException(400, f"That would pass the UGX {conf['daily']:,} 24-hour live limit")
    return msisdn


def _set(c, tid, **f):
    f["updated_at"] = ledger.iso()
    c.execute(f"UPDATE transactions SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), tid))


def _merge_cp(t, extra):
    cp = json.loads(t["counterparty"] or "{}")
    cp.update(extra)
    return json.dumps(cp)


# ------------------------------------------------------------ reconcile
def reconcile(ref: str) -> str:
    """Ask Flutterwave for the truth about one pending live transaction and
    move the ledger accordingly. Safe to call repeatedly."""
    with db.closing() as c:
        t = c.execute("SELECT * FROM transactions WHERE ref=?", (ref,)).fetchone()
    if not t or t["status"] != "pending" or t["rail"] != RAIL:
        return t["status"] if t else "missing"

    outcome, info, reason = None, {}, None
    try:
        if t["type"] == "topup":
            d = provider.verify_charge(t["ref"])["data"]
            st = (d.get("status") or "").lower()
            info = {"flw_id": d.get("id"), "flw_ref": d.get("flw_ref"), "app_fee": d.get("app_fee")}
            if st == "successful":
                if d.get("currency") != "UGX" or int(float(d.get("amount", 0))) < t["amount_minor"]:
                    outcome, reason = "failed", "Amount or currency didn't match — flagged for review"
                else:
                    outcome = "completed"
            elif st == "failed":
                outcome, reason = "failed", d.get("processor_response") or "Payment declined"
        elif t["provider_ref"]:
            d = provider.get_transfer(t["provider_ref"])["data"]
            st = (d.get("status") or "").upper()
            info = {"fee": d.get("fee")}
            if st == "SUCCESSFUL":
                outcome = "completed"
            elif st == "FAILED":
                outcome, reason = "failed", d.get("complete_message") or "Payout failed"
    except FlutterwaveError as e:
        if "No transaction was found" not in str(e):
            return "pending"

    if outcome is None:
        created = ledger.datetime.fromisoformat(t["created_at"])
        if t["type"] == "topup" and ledger.now() - created > timedelta(minutes=PENDING_TIMEOUT_MIN):
            outcome, reason = "failed", "Not approved in time"
        else:
            return "pending"

    with db.write_tx() as c:
        t = c.execute("SELECT * FROM transactions WHERE ref=?", (ref,)).fetchone()
        if t["status"] != "pending":
            return t["status"]
        prov = ledger.system_account(c, "PROVIDER_FLW", LIVE)
        clearing = ledger.system_account(c, "PAYOUT_CLEARING", LIVE)
        amt = t["amount_minor"]
        if t["type"] == "topup" and outcome == "completed":
            ledger._post(c, t["id"], [(prov, -amt), (t["to_account_id"], amt)])
        elif t["type"] == "payout" and outcome == "completed":
            ledger._post(c, t["id"], [(clearing, -amt), (prov, amt)])
        elif t["type"] == "payout" and outcome == "failed":
            ledger._post(c, t["id"], [(clearing, -amt), (t["from_account_id"], amt)])  # refund
        _set(c, t["id"], status=outcome, failure_reason=reason, provider_status=outcome,
             counterparty=_merge_cp(t, {k: v for k, v in info.items() if v is not None}))
    return outcome


def poll_pending() -> int:
    if not_ready_reason():
        return 0
    with db.closing() as c:
        refs = [r["ref"] for r in c.execute("SELECT ref FROM transactions WHERE status='pending' AND rail=?", (RAIL,))]
    for r in refs:
        try:
            reconcile(r)
        except Exception as e:  # never kill the worker
            print("live reconcile error", r, e)
    return len(refs)


# -------------------------------------------------------------- endpoints
def build_router(current_user, txn_out):
    r = APIRouter()

    class CollectIn(BaseModel):
        amount: str
        phone: str

    class PayoutIn(BaseModel):
        amount: str
        phone: str

    @r.get("/api/live/status")
    def status(u=Depends(current_user)):
        conf = cfg()
        if not is_owner(u):
            return {"owner": False}
        with db.write_tx() as c:
            w = live_wallet(c, u["id"])
            bal, used = ledger.balance(c, w["id"]), used_last_24h(c, u["id"])
        return {"owner": True, "ready": not_ready_reason(conf) is None, "reason": not_ready_reason(conf),
                "numbers": [{"msisdn": n, "network": network_of(n)} for n in conf["numbers"]],
                "min": conf["min"], "max": conf["max"], "daily_max": conf["daily"], "used_24h": used,
                "balance": money.from_minor(bal, LIVE), "account_number": w["account_number"]}

    @r.post("/api/live/collections")
    def collect(body: CollectIn, u=Depends(current_user), idempotency_key: str | None = Header(default=None)):
        conf = cfg()
        amount = money.to_minor(body.amount, LIVE)
        with db.write_tx() as c:
            if idempotency_key and (t := ledger._existing(c, u["id"], idempotency_key)):
                return {"transaction": txn_out(c, t, u["id"]), "authorization": json.loads(t["counterparty"] or "{}").get("auth")}
            msisdn = _check(c, conf, u, amount, body.phone)
            w = live_wallet(c, u["id"])
            tid = ledger._insert_txn(c, ref=ledger._new_ref(c, "DEP").replace("SBX-", "MVL-"), user_id=u["id"], type="topup",
                                     status="pending", currency=LIVE, amount_minor=amount,
                                     from_account_id=ledger.system_account(c, "PROVIDER_FLW", LIVE), to_account_id=w["id"],
                                     rail=RAIL, memo="Mobile money deposit", idempotency_key=idempotency_key,
                                     counterparty=json.dumps({"msisdn": msisdn, "network": network_of(msisdn)}),
                                     provider_status="initiating")
            t = c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        try:
            res = provider.charge(msisdn=msisdn, network=network_of(msisdn) or "MTN", amount=amount,
                                  email=u["email"], name=u["full_name"], tx_ref=t["ref"])
            auth = (res.get("meta") or {}).get("authorization") or {}
            with db.write_tx() as c:
                _set(c, tid, provider_status="awaiting_approval", counterparty=_merge_cp(t, {"auth": auth}))
        except FlutterwaveError as e:
            with db.write_tx() as c:
                _set(c, tid, status="failed", provider_status="rejected", failure_reason=str(e))
            auth = None
        with db.closing() as c:
            t = c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
            return {"transaction": txn_out(c, t, u["id"]), "authorization": auth}

    @r.post("/api/live/payouts")
    def payout(body: PayoutIn, u=Depends(current_user), idempotency_key: str | None = Header(default=None)):
        conf = cfg()
        amount = money.to_minor(body.amount, LIVE)
        with db.write_tx() as c:
            if idempotency_key and (t := ledger._existing(c, u["id"], idempotency_key)):
                return {"transaction": txn_out(c, t, u["id"])}
            msisdn = _check(c, conf, u, amount, body.phone)
            w = live_wallet(c, u["id"])
            if ledger.balance(c, w["id"]) < amount:
                raise HTTPException(402, "Insufficient live funds")
            clearing = ledger.system_account(c, "PAYOUT_CLEARING", LIVE)
            tid = ledger._insert_txn(c, ref=ledger._new_ref(c, "OUT").replace("SBX-", "MVL-"), user_id=u["id"], type="payout",
                                     status="pending", currency=LIVE, amount_minor=amount, from_account_id=w["id"],
                                     to_account_id=clearing, rail=RAIL, memo="Mobile money payout",
                                     idempotency_key=idempotency_key, provider_status="initiating",
                                     counterparty=json.dumps({"msisdn": msisdn, "network": network_of(msisdn),
                                                              "account_name": u["full_name"]}))
            ledger._post(c, tid, [(w["id"], -amount), (clearing, amount)])
            t = c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        try:
            res = provider.transfer(msisdn=msisdn, amount=amount, name=u["full_name"], reference=t["ref"],
                                    narration=f"Mvule payout {t['ref']}", bank=conf["payout_bank"])
            with db.write_tx() as c:
                _set(c, tid, provider_ref=str(res["data"]["id"]), provider_status="queued")
        except (FlutterwaveError, KeyError, TypeError) as e:
            with db.write_tx() as c:  # nothing left Flutterwave: refund straight away
                ledger._post(c, tid, [(clearing, -amount), (w["id"], amount)])
                _set(c, tid, status="failed", provider_status="rejected", failure_reason=str(e) or "Payout rejected")
        with db.closing() as c:
            return {"transaction": txn_out(c, c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone(), u["id"])}

    @r.post("/api/live/transactions/{ref}/refresh")
    def refresh(ref: str, u=Depends(current_user)):
        with db.closing() as c:
            t = c.execute("SELECT * FROM transactions WHERE ref=? AND user_id=?", (ref, u["id"])).fetchone()
        if not t:
            raise HTTPException(404, "Transaction not found")
        return {"status": reconcile(ref)}

    @r.get("/api/live/reconciliation")
    def recon(u=Depends(current_user)):
        if not is_owner(u):
            raise HTTPException(403, "Owner only")
        with db.closing() as c:
            users_total = c.execute("SELECT COALESCE(SUM(p.amount_minor),0) s FROM postings p JOIN accounts a ON a.id=p.account_id "
                                    "WHERE a.kind='user' AND a.currency=?", (LIVE,)).fetchone()["s"]
            pend = c.execute("SELECT type, COUNT(*) n, COALESCE(SUM(amount_minor),0) s FROM transactions "
                             "WHERE currency=? AND status='pending' GROUP BY type", (LIVE,)).fetchall()
            done = c.execute("SELECT type, COALESCE(SUM(amount_minor),0) s FROM transactions WHERE currency=? "
                             "AND status='completed' GROUP BY type", (LIVE,)).fetchall()
            in_flight = ledger.balance(c, ledger.system_account(c, "PAYOUT_CLEARING", LIVE))
        prov_bal = None
        if not not_ready_reason():
            try:
                prov_bal = provider.balance().get("data")
            except FlutterwaveError as e:
                prov_bal = {"error": str(e)}
        return {"user_live_balances": users_total, "payouts_in_flight": in_flight,
                "completed": {r["type"]: r["s"] for r in done},
                "pending": {r["type"]: {"count": r["n"], "amount": r["s"]} for r in pend},
                "flutterwave_balance": prov_bal,
                "note": "Flutterwave balance should equal user balances + payouts in flight, minus Flutterwave fees."}

    @r.post("/api/webhooks/flutterwave", include_in_schema=False)
    async def webhook(request: Request):
        conf = cfg()
        if not conf["hash"] or request.headers.get("verif-hash") != conf["hash"]:
            raise HTTPException(401, "Bad signature")
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Bad JSON")
        data = body.get("data") or {}
        ref = data.get("tx_ref") or data.get("reference")
        if ref:
            reconcile(ref)  # re-checks with Flutterwave; payload itself is not trusted
        return {"ok": True}

    return r
