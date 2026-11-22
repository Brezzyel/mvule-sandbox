"""Currencies, amount parsing and the sandbox rails catalogue (all fictional)."""
from decimal import Decimal, InvalidOperation

# ISO-4217 exponent; UGX has no minor unit.
CURRENCIES = {
    "UGX": {"exp": 0, "symbol": "USh", "topup_max": 5_000_000},
    "USD": {"exp": 2, "symbol": "$", "topup_max": 1_000},
    "KES": {"exp": 2, "symbol": "KSh", "topup_max": 100_000},
    "EUR": {"exp": 2, "symbol": "€", "topup_max": 1_000},
    # Real Ugandan shillings via Flutterwave. Owner-only; never mixed with test funds.
    "UGX_LIVE": {"exp": 0, "symbol": "USh", "topup_max": 50_000, "live": True, "label": "UGX · LIVE"},
}
SANDBOX_CURRENCIES = [k for k, v in CURRENCIES.items() if not v.get("live")]


def is_live(cur: str) -> bool:
    return bool(CURRENCIES.get(cur, {}).get("live"))
# A wallet can never hold more than this many top-ups' worth: keeps numbers realistic.
WALLET_CAP_MULTIPLIER = 10


class MoneyError(ValueError):
    pass


def check_currency(cur: str) -> str:
    cur = (cur or "").upper()
    if cur not in CURRENCIES:
        raise MoneyError(f"Unsupported currency {cur!r}")
    return cur


def to_minor(amount: str | int | float, currency: str) -> int:
    exp = CURRENCIES[currency]["exp"]
    try:
        d = Decimal(str(amount).replace(",", "").strip())
    except InvalidOperation:
        raise MoneyError("Amount is not a number")
    if d <= 0:
        raise MoneyError("Amount must be greater than zero")
    q = d.scaleb(exp)
    if q != q.to_integral_value():
        raise MoneyError(f"{currency} allows at most {exp} decimal places")
    return int(q)


def from_minor(minor: int, currency: str) -> str:
    exp = CURRENCIES[currency]["exp"]
    return f"{Decimal(minor).scaleb(-exp):.{exp}f}"


def topup_max_minor(currency: str) -> int:
    return CURRENCIES[currency]["topup_max"] * 10 ** CURRENCIES[currency]["exp"]


# --- Fictional payout rails. Nothing here touches a real network. ---
RAILS = {
    "mockbank": {
        "name": "Mock Bank Transfer",
        "settle_seconds": 20,
        "needs_bank": True,
        "banks": [
            {"code": "LKSD", "name": "Lakeside Demo Bank"},
            {"code": "SVNA", "name": "Savanna Test Bank"},
            {"code": "CRNE", "name": "Crane Sandbox Bank"},
            {"code": "NILE", "name": "Riverbend Example Bank"},
        ],
    },
    "mockmomo": {
        "name": "Mock Mobile Money",
        "settle_seconds": 8,
        "needs_bank": False,
        "banks": [],
    },
}

# Sandbox convention so failure paths can be tested on purpose.
FAIL_SUFFIX = 13  # payouts whose minor amount ends in ...13 are rejected
