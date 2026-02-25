"""
Signed cookie helpers for account session. Step 2.
"""
import base64
import hmac
import hashlib
import secrets
from typing import Any

from app.config import SECRET_KEY

COOKIE_ACCOUNT = "price_sync_account"
COOKIE_OAUTH_STATE = "price_sync_oauth_state"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _sign(value: str) -> str:
    sig = hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def _verify(signed: str) -> str | None:
    if "." not in signed:
        return None
    value, sig = signed.rsplit(".", 1)
    expected = hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return value


def make_account_cookie_value(account_id: str) -> str:
    return _sign(account_id)


def get_account_id_from_cookie(cookie_value: str | None) -> str | None:
    if not cookie_value:
        return None
    return _verify(cookie_value)


def generate_state() -> str:
    return secrets.token_urlsafe(32)
