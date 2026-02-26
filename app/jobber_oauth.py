"""
Jobber OAuth: token exchange, refresh, and account lookup. Step 2 + Step 3.
"""
import datetime
import urllib.parse
from typing import Any

import requests

from app.config import JOBBER_CLIENT_ID, JOBBER_CLIENT_SECRET

JOBBER_TOKEN_URL = "https://api.getjobber.com/api/oauth/token"
JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
JOBBER_GRAPHQL_VERSION = "2026-02-17"
ACCOUNT_QUERY = "query { account { id name } }"

# Step 6: tell Jobber to mark the app as disconnected for this account
APP_DISCONNECT_MUTATION = "mutation { appDisconnect { success userErrors { message } } }"

# Step 3: refresh when token expires within this many seconds
REFRESH_BUFFER_SECONDS = 120


def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict[str, Any]:
    """
    Exchange authorization code for access_token and refresh_token.
    Returns dict with access_token, refresh_token; raises on error.
    """
    response = requests.post(
        JOBBER_TOKEN_URL,
        data={
            "client_id": JOBBER_CLIENT_ID,
            "client_secret": JOBBER_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if "access_token" not in data or "refresh_token" not in data:
        raise ValueError("Token response missing access_token or refresh_token")
    return data


def get_account_info(access_token: str) -> dict[str, str]:
    """
    Call Jobber GraphQL account query; return {"id": ..., "name": ...}.
    """
    response = requests.post(
        JOBBER_GRAPHQL_URL,
        json={"query": ACCOUNT_QUERY},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-JOBBER-GRAPHQL-VERSION": JOBBER_GRAPHQL_VERSION,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise ValueError(data["errors"][0].get("message", "GraphQL error"))
    account = (data.get("data") or {}).get("account")
    if not account:
        raise ValueError("Account not in response")
    return {"id": account.get("id", ""), "name": (account.get("name") or "").strip()}


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """
    Step 3: Exchange refresh_token for new access_token and refresh_token.
    Jobber uses refresh token rotation: always save the new refresh_token.
    Returns dict with access_token, refresh_token; optionally expires_in (seconds).
    Raises on error (e.g. 401 = refresh token invalid, reconnect required).
    """
    response = requests.post(
        JOBBER_TOKEN_URL,
        data={
            "client_id": JOBBER_CLIENT_ID,
            "client_secret": JOBBER_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if "access_token" not in data or "refresh_token" not in data:
        raise ValueError("Token response missing access_token or refresh_token")
    return data


def get_valid_access_token(account_id: str) -> str:
    """
    Step 3: Return a valid access_token for this account. Refreshes proactively
    if access_token_expires_at is in the past or within REFRESH_BUFFER_SECONDS.
    On 401 from Jobber, caller should refresh (refresh_access_token + update_tokens) and retry once.
    Raises ValueError if no connection or refresh fails (reconnect required).
    """
    from app.database import get_connection_by_account_id, update_tokens

    conn_row = get_connection_by_account_id(account_id)
    if not conn_row:
        raise ValueError("No connection for account; reconnect required")
    access_token = conn_row.get("access_token") or ""
    refresh_token = conn_row.get("refresh_token") or ""
    expires_at_str = conn_row.get("access_token_expires_at")

    if not access_token or not refresh_token:
        raise ValueError("Missing tokens; reconnect required")

    # Proactive refresh if we have expires_at and it's in the past or within buffer
    now = datetime.datetime.now(datetime.UTC)
    if expires_at_str:
        try:
            # ISO format with Z or +00:00
            expires_at = datetime.datetime.fromisoformat(
                expires_at_str.replace("Z", "+00:00")
            )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=datetime.UTC)
            if (expires_at - now).total_seconds() <= REFRESH_BUFFER_SECONDS:
                data = refresh_access_token(refresh_token)
                new_access = data["access_token"]
                new_refresh = data["refresh_token"]
                expires_in = data.get("expires_in")
                new_expires_at = None
                if expires_in is not None:
                    new_expires_at = (
                        now + datetime.timedelta(seconds=int(expires_in))
                    ).isoformat().replace("+00:00", "Z")
                update_tokens(account_id, new_access, new_refresh, new_expires_at)
                return new_access
        except Exception:
            pass  # use current token; caller may get 401 and retry with refresh

    return access_token


def build_authorize_url(redirect_uri: str, state: str) -> str:
    """Build Jobber OAuth authorize URL (user visits this to connect)."""
    params = {
        "response_type": "code",
        "client_id": JOBBER_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return "https://api.getjobber.com/api/oauth/authorize?" + urllib.parse.urlencode(params)


def call_app_disconnect(access_token: str) -> None:
    """
    Step 6: Call Jobber's appDisconnect mutation so Jobber marks the app as disconnected.
    Uses the given access_token (for the account to disconnect). Raises on HTTP/GraphQL error.
    """
    response = requests.post(
        JOBBER_GRAPHQL_URL,
        json={"query": APP_DISCONNECT_MUTATION},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-JOBBER-GRAPHQL-VERSION": JOBBER_GRAPHQL_VERSION,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise ValueError(data["errors"][0].get("message", "GraphQL error"))
    result = (data.get("data") or {}).get("appDisconnect")
    if result and result.get("userErrors"):
        msg = result["userErrors"][0].get("message", "appDisconnect failed")
        raise ValueError(msg)
