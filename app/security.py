import hashlib
import hmac
import os
import secrets
import time

import jwt

_SECRET_FILE = os.path.join(os.path.dirname(__file__), "..", "data", ".jwt_secret")
TOKEN_TTL = 60 * 60 * 12


def _secret() -> str:
    env = os.environ.get("MVULE_JWT_SECRET")
    if env:
        return env
    path = os.path.abspath(_SECRET_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_urlsafe(48))
    with open(path) as f:
        return f.read().strip()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 240_000)
    return f"pbkdf2$240000${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, iters, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(calc.hex(), dk)
    except ValueError:
        return False


def issue_token(user_id: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + TOKEN_TTL}, _secret(), algorithm="HS256")


def read_token(token: str) -> int | None:
    try:
        return int(jwt.decode(token, _secret(), algorithms=["HS256"])["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
