"""
Step 4: Sync CSV pricing to Jobber. Shared logic for web app (and optional CLI).
Uses get_valid_access_token(account_id); on 401 refreshes and retries once.
"""
import csv
import io
import json
import time
from typing import Any

import requests

# GraphQL (same as sync_prices_to_jobber.py)
JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
RATE_LIMIT_SLEEP_SEC = 0.5
GRAPHQL_VERSION = "2026-02-17"

# Enhancement 2: include internalUnitCost so we can compare (price protection: only update if new > current)
QUERY_PRODUCTS_PAGE = """
query GetProductsPage($first: Int!, $after: String) {
  productOrServices(first: $first, after: $after) {
    nodes {
      id
      name
      internalUnitCost
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

# Enhancement 1 + 2: code (SKU) and internalUnitCost
QUERY_PRODUCTS_PAGE_WITH_CODE = """
query GetProductsPageWithCode($first: Int!, $after: String) {
  productOrServices(first: $first, after: $after) {
    nodes {
      id
      name
      code
      internalUnitCost
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

MUTATION_UPDATE_COST = """
mutation UpdateProductCost($productOrServiceId: EncodedId!, $internalUnitCost: Float!) {
  productsAndServicesEdit(productOrServiceId: $productOrServiceId, input: { internalUnitCost: $internalUnitCost }) {
    productOrService {
      id
    }
    userErrors {
      message
      path
    }
  }
}
"""


class TokenExpiredError(Exception):
    """Raised when Jobber returns 401; caller should refresh and retry."""


def parse_csv_from_bytes(content: bytes) -> list[tuple[str, float]]:
    """
    Parse CSV from bytes. Columns Part_Num (product name) and Trade_Cost (unit cost).
    Returns list of (sku, cost). Raises ValueError if columns missing or no valid rows.
    """
    required = {"Part_Num", "Trade_Cost"}
    rows: list[tuple[str, float]] = []
    with io.BytesIO(content) as buf:
        text = buf.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not required.issubset(reader.fieldnames or set()):
        raise ValueError("CSV must contain columns Part_Num and Trade_Cost")
    for row in reader:
        part_num = (row.get("Part_Num") or "").strip()
        trade_cost_raw = (row.get("Trade_Cost") or "").strip()
        if not part_num and not trade_cost_raw:
            continue
        if not part_num:
            continue
        try:
            cost = float(trade_cost_raw.replace(",", ""))
        except ValueError:
            continue
        rows.append((part_num, cost))
    if not rows:
        raise ValueError("No valid rows to process")
    return rows


def _build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-JOBBER-GRAPHQL-VERSION": GRAPHQL_VERSION,
    }


def _graphql_request(
    session: requests.Session,
    headers: dict[str, str],
    query: str,
    variables: dict[str, Any] | None = None,
) -> requests.Response:
    payload: dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    return session.post(JOBBER_GRAPHQL_URL, headers=headers, json=payload, timeout=30)


def _probe_code_available(session: requests.Session, headers: dict[str, str]) -> bool:
    """Enhancement 1: One request with code field. Returns True if schema supports it (no GraphQL errors)."""
    variables: dict[str, Any] = {"first": 1, "after": None}
    resp = _graphql_request(session, headers, QUERY_PRODUCTS_PAGE_WITH_CODE, variables)
    if resp.status_code == 401:
        raise TokenExpiredError()
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return False
    if data.get("errors"):
        return False
    return True


def _parse_current_cost(node: dict) -> float | None:
    """Enhancement 2: Extract internalUnitCost from node; None if missing or invalid."""
    val = node.get("internalUnitCost")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _find_id_by_sku(
    session: requests.Session,
    headers: dict[str, str],
    sku: str,
    match_by_code_first: bool = False,
) -> tuple[str | None, float | None]:
    """Paginate through productOrServices; return (node id, current internalUnitCost or None) where code or name matches sku. Raises TokenExpiredError on 401.
    Enhancement 1: when match_by_code_first, try code match first then name. Enhancement 2: also return current cost for price protection."""
    query = QUERY_PRODUCTS_PAGE_WITH_CODE if match_by_code_first else QUERY_PRODUCTS_PAGE
    after: str | None = None
    while True:
        variables: dict[str, Any] = {"first": 100, "after": after}
        resp = _graphql_request(session, headers, query, variables)
        if resp.status_code == 401:
            raise TokenExpiredError()
        if resp.status_code != 200:
            return (None, None)
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return (None, None)
        if data.get("errors"):
            return (None, None)
        conn = data.get("data", {}).get("productOrServices")
        if not conn:
            return (None, None)
        nodes = conn.get("nodes")
        if nodes is None and conn.get("edges") is not None:
            nodes = [e.get("node") for e in conn["edges"] if e.get("node")]
        nodes = nodes or []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if match_by_code_first and (node.get("code") or "").strip() == sku:
                return (node.get("id"), _parse_current_cost(node))
            if (node.get("name") or "").strip() == sku:
                return (node.get("id"), _parse_current_cost(node))
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return (None, None)
        after = page_info.get("endCursor")
        if not after:
            return (None, None)
        time.sleep(RATE_LIMIT_SLEEP_SEC)


def _update_unit_cost(
    session: requests.Session,
    headers: dict[str, str],
    node_id: str,
    cost: float,
) -> bool:
    """Run mutation to set internalUnitCost. Returns True on success. Raises TokenExpiredError on 401."""
    variables = {"productOrServiceId": node_id, "internalUnitCost": cost}
    resp = _graphql_request(session, headers, MUTATION_UPDATE_COST, variables)
    if resp.status_code == 401:
        raise TokenExpiredError()
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return False
    if data.get("errors"):
        return False
    payload = data.get("data", {}).get("productsAndServicesEdit") or {}
    user_errors = payload.get("userErrors") or []
    return len(user_errors) == 0


def run_sync(
    account_id: str,
    rows: list[tuple[str, float]],
    only_increase_cost: bool = False,
) -> dict[str, Any]:
    """
    Run sync for the given account_id and CSV rows. Uses get_valid_access_token;
    on 401 refreshes and retries that request once.
    Returns {"updated": int, "skus_not_found": list[str], "skipped_protected": int, "error": str | None}.
    Enhancement 2: when only_increase_cost=True, skip update when new cost <= current cost (count in skipped_protected).
    """
    from app.database import get_connection_by_account_id, update_tokens
    from app.jobber_oauth import get_valid_access_token, refresh_access_token

    result: dict[str, Any] = {"updated": 0, "skus_not_found": [], "skipped_protected": 0, "error": None}
    conn_row = get_connection_by_account_id(account_id)
    if not conn_row:
        result["error"] = "Not connected; please connect to Jobber first."
        return result

    try:
        token = get_valid_access_token(account_id)
    except ValueError as e:
        result["error"] = str(e)
        return result

    headers = _build_headers(token)
    session = requests.Session()

    # Enhancement 1: probe whether Jobber schema supports product code (SKU); then match by code first or name only
    try:
        match_by_code_first = _probe_code_available(session, headers)
    except TokenExpiredError:
        result["error"] = "Session expired; please reconnect to Jobber."
        return result
    time.sleep(RATE_LIMIT_SLEEP_SEC)

    def refresh_and_retry():
        nonlocal token, headers
        try:
            data = refresh_access_token(conn_row["refresh_token"])
        except Exception as e:
            result["error"] = f"Session expired; please reconnect to Jobber. ({e})"
            return None
        import datetime
        new_access = data["access_token"]
        new_refresh = data["refresh_token"]
        expires_in = data.get("expires_in")
        new_expires_at = None
        if expires_in is not None:
            now = datetime.datetime.now(datetime.UTC)
            new_expires_at = (now + datetime.timedelta(seconds=int(expires_in))).isoformat().replace("+00:00", "Z")
        update_tokens(account_id, new_access, new_refresh, new_expires_at)
        conn_row["access_token"] = new_access
        conn_row["refresh_token"] = new_refresh
        token = new_access
        headers = _build_headers(token)
        return token

    for sku, cost in rows:
        if result["error"]:
            break
        try:
            node_id, current_cost = _find_id_by_sku(session, headers, sku, match_by_code_first=match_by_code_first)
        except TokenExpiredError:
            if refresh_and_retry() is None:
                break
            try:
                node_id, current_cost = _find_id_by_sku(session, headers, sku, match_by_code_first=match_by_code_first)
            except TokenExpiredError:
                result["error"] = "Session expired; please reconnect to Jobber."
                break
        time.sleep(RATE_LIMIT_SLEEP_SEC)

        if node_id is None:
            result["skus_not_found"].append(sku)
            continue

        # Enhancement 2: only update when new cost is higher than current (current null/zero -> allow update)
        if only_increase_cost and current_cost is not None and cost <= current_cost:
            result["skipped_protected"] += 1
            continue

        try:
            ok = _update_unit_cost(session, headers, node_id, cost)
        except TokenExpiredError:
            if refresh_and_retry() is None:
                break
            try:
                ok = _update_unit_cost(session, headers, node_id, cost)
            except TokenExpiredError:
                result["error"] = "Session expired; please reconnect to Jobber."
                break
        time.sleep(RATE_LIMIT_SLEEP_SEC)

        if not ok:
            result["skus_not_found"].append(sku)
        else:
            result["updated"] += 1

    return result


def run_sync_preview(account_id: str, rows: list[tuple[str, float]]) -> dict[str, Any]:
    """
    Enhancement 3: Dry-run. Same matching as run_sync, no mutations. Returns counts of
    increases, decreases, unchanged, and skus_not_found. Used for "Preview" before apply.
    Returns {"increases": int, "decreases": int, "unchanged": int, "skus_not_found": list[str], "error": str | None}.
    """
    from app.database import get_connection_by_account_id, update_tokens
    from app.jobber_oauth import get_valid_access_token, refresh_access_token

    result: dict[str, Any] = {
        "increases": 0,
        "decreases": 0,
        "unchanged": 0,
        "skus_not_found": [],
        "error": None,
    }
    conn_row = get_connection_by_account_id(account_id)
    if not conn_row:
        result["error"] = "Not connected; please connect to Jobber first."
        return result

    try:
        token = get_valid_access_token(account_id)
    except ValueError as e:
        result["error"] = str(e)
        return result

    headers = _build_headers(token)
    session = requests.Session()

    try:
        match_by_code_first = _probe_code_available(session, headers)
    except TokenExpiredError:
        result["error"] = "Session expired; please reconnect to Jobber."
        return result
    time.sleep(RATE_LIMIT_SLEEP_SEC)

    def refresh_and_retry():
        nonlocal token, headers
        try:
            data = refresh_access_token(conn_row["refresh_token"])
        except Exception as e:
            result["error"] = f"Session expired; please reconnect to Jobber. ({e})"
            return None
        import datetime
        new_access = data["access_token"]
        new_refresh = data["refresh_token"]
        expires_in = data.get("expires_in")
        new_expires_at = None
        if expires_in is not None:
            now = datetime.datetime.now(datetime.UTC)
            new_expires_at = (now + datetime.timedelta(seconds=int(expires_in))).isoformat().replace("+00:00", "Z")
        update_tokens(account_id, new_access, new_refresh, new_expires_at)
        conn_row["access_token"] = new_access
        conn_row["refresh_token"] = new_refresh
        token = new_access
        headers = _build_headers(token)
        return token

    for sku, cost in rows:
        if result["error"]:
            break
        try:
            node_id, current_cost = _find_id_by_sku(session, headers, sku, match_by_code_first=match_by_code_first)
        except TokenExpiredError:
            if refresh_and_retry() is None:
                break
            try:
                node_id, current_cost = _find_id_by_sku(session, headers, sku, match_by_code_first=match_by_code_first)
            except TokenExpiredError:
                result["error"] = "Session expired; please reconnect to Jobber."
                break
        time.sleep(RATE_LIMIT_SLEEP_SEC)

        if node_id is None:
            result["skus_not_found"].append(sku)
            continue

        effective_current = current_cost if current_cost is not None else 0.0
        if cost > effective_current:
            result["increases"] += 1
        elif cost < effective_current:
            result["decreases"] += 1
        else:
            result["unchanged"] += 1

    return result
