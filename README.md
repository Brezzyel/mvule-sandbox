# Mvule Pay — Sandbox

A working payments sandbox: FastAPI backend with a double-entry ledger, plus a mobile-first web client.
Wallets start at 0. No real money moves. All banks and payout rails are fictional.

## Run locally
    pip install -r requirements.txt
    uvicorn app.main:app --reload
Open http://localhost:8000. API docs at http://localhost:8000/docs

## Tests
    pip install pytest httpx && pytest -q

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
