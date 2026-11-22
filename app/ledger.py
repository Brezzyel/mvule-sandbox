"""Double-entry ledger. Every movement is a transaction with postings that sum
to zero. Balances are always derived from postings, never stored."""
import json
import random
import secrets
import sqlite3
import string
from datetime import datetime, timedelta, timezone

from . import money

SYSTEM_ACCOUNTS = {
    "FAUCET": "Sandbox test-funds faucet",
    "PAYOUT_CLEARING": "Payouts in flight",
    "PAYOUT_SETTLED": "Settled to mock rails",
    "PROVIDER_FLW": "Real funds collected via Flutterwave",
}


class LedgerError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="seconds")


def _new_account_number(c: sqlite3.Connection) -> str:
    while True:
        n = "70" + "".join(random.choices(string.digits, k=8))
        if not c.execute("SELECT 1 FROM accounts WHERE account_number=?", (n,)).fetchone():
            return n


def _new_ref(c: sqlite3.Connection, prefix: str) -> str:
    while True:
        r = f"SBX-{prefix}-{secrets.token_hex(4).upper()}"
        if not c.execute("SELECT 1 FROM transactions WHERE ref=?", (r,)).fetchone():
            return r


def ensure_system_accounts(c: sqlite3.Connection) -> None:
    for code, name in SYSTEM_ACCOUNTS.items():
        for cur in money.CURRENCIES:
            if not c.execute("SELECT 1 FROM accounts WHERE code=? AND currency=?", (code, cur)).fetchone():
                c.execute(
                    "INSERT INTO accounts(kind,code,name,currency,account_number,created_at) VALUES('system',?,?,?,?,?)",
                    (code, name, cur, _new_account_number(c), iso()),
                )


def system_account(c, code: str, cur: str) -> int:
    return c.execute("SELECT id FROM accounts WHERE code=? AND currency=?", (code, cur)).fetchone()["id"]


def open_wallets(c, user_id: int, currencies=None) -> None:
    for cur in currencies or money.SANDBOX_CURRENCIES:
        c.execute(
            "INSERT OR IGNORE INTO accounts(user_id,kind,name,currency,account_number,created_at) VALUES(?,'user',?,?,?,?)",
            (user_id, f"{cur} wallet", cur, _new_account_number(c), iso()),
        )


def balance(c, account_id: int) -> int:
    return c.execute("SELECT COALESCE(SUM(amount_minor),0) b FROM postings WHERE account_id=?", (account_id,)).fetchone()["b"]


def wallet(c, user_id: int, cur: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM accounts WHERE user_id=? AND currency=?", (user_id, cur)).fetchone()
    if not row:
        raise LedgerError("Wallet not found", 404)
    return row


def _post(c, txn_id: int, legs: list[tuple[int, int]]) -> None:
    assert sum(a for _, a in legs) == 0, "postings must balance"
    for acct, amt in legs:
        c.execute("INSERT INTO postings(txn_id,account_id,amount_minor,created_at) VALUES(?,?,?,?)", (txn_id, acct, amt, iso()))


def _existing(c, user_id, idem):
    if not idem:
        return None
    return c.execute("SELECT * FROM transactions WHERE user_id=? AND idempotency_key=?", (user_id, idem)).fetchone()


def _insert_txn(c, **f) -> int:
    f.setdefault("created_at", iso())
    f.setdefault("updated_at", f["created_at"])
    cols = ",".join(f)
    cur = c.execute(f"INSERT INTO transactions({cols}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
    return cur.lastrowid


# ---------------------------------------------------------------- operations

def _no_live(cur):
    if money.is_live(cur):
        raise LedgerError("Live funds use the live endpoints")


def topup(c, user_id: int, cur: str, amount_minor: int, idem: str | None):
    _no_live(cur)
    if (t := _existing(c, user_id, idem)):
        return t
    if amount_minor > money.topup_max_minor(cur):
        raise LedgerError(f"Sandbox top-ups are limited to {money.from_minor(money.topup_max_minor(cur), cur)} {cur}")
    w = wallet(c, user_id, cur)
    cap = money.topup_max_minor(cur) * money.WALLET_CAP_MULTIPLIER
    if balance(c, w["id"]) + amount_minor > cap:
        raise LedgerError(f"Sandbox wallets are capped at {money.from_minor(cap, cur)} {cur}")
    tid = _insert_txn(c, ref=_new_ref(c, "TOP"), user_id=user_id, type="topup", status="completed", currency=cur,
                      amount_minor=amount_minor, from_account_id=system_account(c, "FAUCET", cur), to_account_id=w["id"],
                      rail="sandbox", memo="Sandbox test funds", idempotency_key=idem)
    _post(c, tid, [(system_account(c, "FAUCET", cur), -amount_minor), (w["id"], amount_minor)])
    return c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()


def find_recipient(c, q: str):
    q = (q or "").strip()
    return c.execute(
        "SELECT u.* FROM users u WHERE u.email=? COLLATE NOCASE OR u.phone=? "
        "OR u.id=(SELECT user_id FROM accounts WHERE account_number=? AND kind='user')",
        (q, q, q),
    ).fetchone()


def transfer(c, user_id: int, cur: str, amount_minor: int, recipient_q: str, memo: str | None, idem: str | None):
    if money.is_live(cur):
        raise LedgerError("Live funds can't be sent to other users")
    if (t := _existing(c, user_id, idem)):
        return t
    rcpt = find_recipient(c, recipient_q)
    if not rcpt:
        raise LedgerError("No Mvule user with that email, phone or account number", 404)
    if rcpt["id"] == user_id:
        raise LedgerError("You can't send money to yourself")
    src, dst = wallet(c, user_id, cur), wallet(c, rcpt["id"], cur)
    if balance(c, src["id"]) < amount_minor:
        raise LedgerError("Insufficient funds", 402)
    tid = _insert_txn(c, ref=_new_ref(c, "TRF"), user_id=user_id, type="transfer", status="completed", currency=cur,
                      amount_minor=amount_minor, from_account_id=src["id"], to_account_id=dst["id"], rail="internal",
                      counterparty=json.dumps({"name": rcpt["full_name"], "account_number": dst["account_number"]}),
                      memo=memo, idempotency_key=idem)
    _post(c, tid, [(src["id"], -amount_minor), (dst["id"], amount_minor)])
    return c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()


def payout(c, user_id: int, cur: str, amount_minor: int, rail: str, details: dict, memo: str | None, idem: str | None):
    _no_live(cur)
    if (t := _existing(c, user_id, idem)):
        return t
    r = money.RAILS.get(rail)
    if not r:
        raise LedgerError("Unknown payout rail")
    if r["needs_bank"]:
        bank = next((b for b in r["banks"] if b["code"] == details.get("bank_code")), None)
        if not bank:
            raise LedgerError("Choose a sandbox bank")
        details["bank_name"] = bank["name"]
    if not (details.get("account_number") and details.get("account_name")):
        raise LedgerError("Account name and number are required")
    src = wallet(c, user_id, cur)
    if balance(c, src["id"]) < amount_minor:
        raise LedgerError("Insufficient funds", 402)
    clearing = system_account(c, "PAYOUT_CLEARING", cur)
    tid = _insert_txn(c, ref=_new_ref(c, "PAY"), user_id=user_id, type="payout", status="pending", currency=cur,
                      amount_minor=amount_minor, from_account_id=src["id"], to_account_id=clearing, rail=rail,
                      counterparty=json.dumps(details), memo=memo, idempotency_key=idem,
                      settle_at=iso(now() + timedelta(seconds=r["settle_seconds"])))
    _post(c, tid, [(src["id"], -amount_minor), (clearing, amount_minor)])
    return c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()


def settle_due(c) -> int:
    """Called by the background worker. Completes or fails pending payouts."""
    due = c.execute("SELECT * FROM transactions WHERE status='pending' AND settle_at<=? AND rail IN ('mockbank','mockmomo')",
                    (iso(),)).fetchall()
    for t in due:
        cur, amt = t["currency"], t["amount_minor"]
        clearing = system_account(c, "PAYOUT_CLEARING", cur)
        if amt % 100 == money.FAIL_SUFFIX:
            _post(c, t["id"], [(clearing, -amt), (t["from_account_id"], amt)])  # refund
            c.execute("UPDATE transactions SET status='failed', failure_reason=?, updated_at=? WHERE id=?",
                      ("Rejected by sandbox rail (amount ends in 13)", iso(), t["id"]))
        else:
            _post(c, t["id"], [(clearing, -amt), (system_account(c, "PAYOUT_SETTLED", cur), amt)])
            c.execute("UPDATE transactions SET status='completed', updated_at=? WHERE id=?", (iso(), t["id"]))
    return len(due)


def check_integrity(c) -> bool:
    """Every transaction's postings sum to zero."""
    bad = c.execute("SELECT txn_id FROM postings GROUP BY txn_id HAVING SUM(amount_minor)<>0").fetchall()
    return not bad
