"""
Jobber OAuth: token exchange and account lookup. Step 2.
"""
import urllib.parse
from typing import Any

import requests

from app.config import JOBBER_CLIENT_ID, JOBBER_CLIENT_SECRET

JOBBER_TOKEN_URL = "https://api.getjobber.com/api/oauth/token"
JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
JOBBER_GRAPHQL_VERSION = "2026-02-17"
ACCOUNT_QUERY = "query { account { id name } }"


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


def build_authorize_url(redirect_uri: str, state: str) -> str:
    """Build Jobber OAuth authorize URL (user visits this to connect)."""
    params = {
        "response_type": "code",
        "client_id": JOBBER_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return "https://api.getjobber.com/api/oauth/authorize?" + urllib.parse.urlencode(params)
