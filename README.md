# Mvule Pay — Sandbox

A working payments sandbox: FastAPI backend with a double-entry ledger, plus a mobile-first web client.
Wallets start at 0. No real money moves. All banks and payout rails are fictional.

## Run locally
    pip install -r requirements.txt
    uvicorn app.main:app --reload
Open http://localhost:8000. API docs at http://localhost:8000/docs

## Tests
    pip install pytest httpx
    pytest -q tests/test_api.py && pytest -q tests/test_live.py

## What it does
- Sign up / sign in (PBKDF2 password hashing, JWT, login lockout after 5 fails)
- UGX, USD, KES, EUR wallets per user, all starting at 0
- Test funds: capped per top-up and per wallet so numbers stay realistic
- Instant transfers between users (by email, phone or account number), with name lookup
- Payouts to "Mock Mobile Money" (~8s) and "Mock Bank Transfer" (~20s), settled by a background worker
- Failure testing: payouts ending in .13 (UGX: 13) are rejected and refunded automatically
- Idempotency-Key header on every money-moving call (no double charges on retry)
- Balances are always derived from ledger postings; /api/health checks the ledger balances to zero
- Saved recipients, transaction history, receipts (watermarked SANDBOX), change password

## Endpoints
POST /api/auth/register · POST /api/auth/login · GET /api/me · POST /api/me/password
GET /api/wallets · GET /api/meta · GET /api/transactions · GET /api/transactions/{ref}
POST /api/topups · POST /api/transfers · POST /api/payouts · GET /api/recipients/lookup?q=
GET/POST/DELETE /api/payees · GET /api/health

## Config (env)
MVULE_DB (sqlite path) · MVULE_JWT_SECRET (set this in production) · MVULE_CORS (comma-separated origins) · MVULE_SETTLE_INTERVAL

## Deploy
Netlify can't run Python, so host the backend on Render, Railway or Fly.io (Dockerfile included; mount a disk at /data).
The backend serves the web app at /. To host the frontend on Netlify instead, deploy web/ and open it once with
?api=https://your-backend-url — it remembers the API address.

## Live mode (real UGX via Flutterwave, owner only)

A separate "UGX · LIVE" wallet that only the owner sees. Deposits come from your own MTN/Airtel
number (PIN prompt on your phone); withdrawals go back to your own numbers. Live money can't be
sent to other users. It stays OFF until every setting below is in place.

Set these in Render → mvule-sandbox → Environment:

| Variable | Example | Meaning |
|---|---|---|
| MVULE_LIVE | 1 | Master switch (0 or unset = off) |
| FLW_SECRET_KEY | FLWSECK_TEST-... then FLWSECK-... | Flutterwave secret key (test first, live later) |
| FLW_SECRET_HASH | any long random string | Same value as "Secret hash" in Flutterwave → Settings → Webhooks |
| LIVE_OWNER_EMAILS | you@example.com | Mvule account email(s) allowed to use live mode |
| LIVE_ALLOWED_NUMBERS | 0772xxxxxx,0701xxxxxx | Your own MTN/Airtel numbers |
| LIVE_MAX_TXN / LIVE_MIN_TXN | 50000 / 500 | Per-transaction limits (UGX) |
| LIVE_DAILY_MAX | 200000 | Rolling 24h limit (UGX) |
| MVULE_PERSISTENT_DISK | 1 | Set only after adding a Render disk mounted at /data |
| FLW_UG_PAYOUT_BANK | MPS | Flutterwave mobile-money payout code for Uganda |

Flutterwave webhook URL: https://mvule-sandbox.onrender.com/api/webhooks/flutterwave

Safety: every webhook is re-checked against the Flutterwave API before the ledger moves; pending
items are also polled every ~15s; failed payouts are refunded; owner-only reconciliation at
GET /api/live/reconciliation.
