#!/usr/bin/env python3
"""
Sync unit costs from wholesaler_prices.csv to Jobber via GraphQL.

Maps CSV Part_Num -> SKU, Trade_Cost -> unit cost. For each row: query Jobber
for the product/service by SKU to get its id, then run a mutation to update
internalUnitCost. Exact GraphQL query/mutation names must be verified in
Jobber's GraphiQL (Developer Center -> Manage Apps -> Test in GraphiQL).
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


# -----------------------------------------------------------------------------
# GraphQL operations (verify names and args in Jobber GraphiQL before use)
# -----------------------------------------------------------------------------

# Query: fetch products/services with id and name for matching.
# Jobber's ProductOrService has no "sku" field; we match CSV Part_Num to name.
QUERY_PRODUCTS_PAGE = """
query GetProductsPage($first: Int!, $after: String) {
  productOrServices(first: $first, after: $after) {
    nodes {
      id
      name
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

# Mutation: update unit cost (Jobber 2026: productOrServiceId + input with internalUnitCost).
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


JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
RATE_LIMIT_SLEEP_SEC = 0.5
API_VERSION_HEADER = "2026-02-17"

# ANSI yellow for warnings (Windows-friendly: still readable if no ANSI)
YELLOW = "\033[33m"
RESET = "\033[0m"

_permission_hint_shown = False


def load_token() -> str:
    """Load JOBBER_ACCESS_TOKEN from .env; exit if missing or empty."""
    # Load from script directory so it works regardless of cwd
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env")
    token = os.environ.get("JOBBER_ACCESS_TOKEN", "").strip()
    if not token:
        print("Error: JOBBER_ACCESS_TOKEN is not set or is empty.", file=sys.stderr)
        print("Create a .env file with JOBBER_ACCESS_TOKEN=your_token", file=sys.stderr)
        sys.exit(1)
    return token


def build_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-JOBBER-GRAPHQL-VERSION": API_VERSION_HEADER,
    }


def graphql_request(
    session: requests.Session,
    headers: dict,
    query: str,
    variables: dict | None = None,
) -> requests.Response:
    """POST a GraphQL request; returns response. Caller checks status and body."""
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    return session.post(JOBBER_GRAPHQL_URL, headers=headers, json=payload)


def check_fatal_status(response: requests.Response) -> None:
    """If status is 401 or 500 (or 429), print raw response and exit."""
    if response.status_code not in (401, 429, 500):
        return
    print("Fatal API response. Raw body below.", file=sys.stderr)
    try:
        raw = response.json()
        print(json.dumps(raw, indent=2), file=sys.stderr)
    except Exception:
        print(response.text, file=sys.stderr)
    sys.exit(1)


def find_id_by_sku(
    session: requests.Session,
    headers: dict,
    sku: str,
) -> str | None:
    """
    Paginate through productsAndServices until we find a node with matching sku.
    Returns the node id or None if not found. Exits on 401/500.
    """
    after = None
    while True:
        variables = {"first": 100, "after": after}
        resp = graphql_request(
            session, headers, QUERY_PRODUCTS_PAGE, variables
        )
        check_fatal_status(resp)
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return None
        if "errors" in data and data["errors"]:
            err_msg = (data["errors"][0].get("message") or "")
            if "permissions" in err_msg.lower():
                if not _permission_hint_shown:
                    globals()["_permission_hint_shown"] = True
                    print(
                        "Jobber returned: ProductOrService hidden due to permissions. "
                        "Add Products/Services scopes to your app and re-authorize (Test in GraphiQL).",
                        file=sys.stderr,
                    )
            else:
                print(json.dumps(data, indent=2), file=sys.stderr)
            return None
        conn = data.get("data", {}).get("productOrServices")
        if not conn:
            return None
        # Support both nodes and Relay-style edges { node { id name } }
        nodes = conn.get("nodes")
        if nodes is None and conn.get("edges") is not None:
            nodes = [e.get("node") for e in conn["edges"] if e.get("node")]
        nodes = nodes or []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if (node.get("name") or "").strip() == sku:
                return node.get("id")
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return None
        after = page_info.get("endCursor")
        if not after:
            return None
        time.sleep(RATE_LIMIT_SLEEP_SEC)


def update_unit_cost(
    session: requests.Session,
    headers: dict,
    node_id: str,
    cost: float,
) -> bool:
    """Run mutation to set internalUnitCost. Returns True on success."""
    variables = {"productOrServiceId": node_id, "internalUnitCost": cost}
    resp = graphql_request(session, headers, MUTATION_UPDATE_COST, variables)
    check_fatal_status(resp)
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return False
    if data.get("errors"):
        print(json.dumps(data.get("errors"), indent=2), file=sys.stderr)
        return False
    payload = data.get("data", {}).get("productsAndServicesEdit") or {}
    user_errors = payload.get("userErrors") or []
    return len(user_errors) == 0


def warn_sku_not_found(sku: str) -> None:
    print(f"{YELLOW}SKU [{sku}] not found in Jobber, skipping...{RESET}", file=sys.stderr)


def load_and_clean_csv(csv_path: Path) -> list[tuple[str, float]]:
    """
    Read CSV; map Part_Num -> SKU, Trade_Cost -> float. Skip blank rows and
    invalid rows. Return list of (sku, cost).
    """
    required = {"Part_Num", "Trade_Cost"}
    rows: list[tuple[str, float]] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not required.issubset(reader.fieldnames or set()):
            print("Error: CSV must contain columns Part_Num and Trade_Cost.", file=sys.stderr)
            sys.exit(1)
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
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync CSV pricing to Jobber GraphQL.")
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("wholesaler_prices.csv"),
        help="Path to wholesaler_prices CSV (default: wholesaler_prices.csv)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only read CSV and print what would be updated; no API calls.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print first API response (product list) and exit; for troubleshooting.",
    )
    args = parser.parse_args()

    if not args.csv.is_file():
        print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    rows = load_and_clean_csv(args.csv)
    if not rows:
        print("No valid rows to process.", file=sys.stderr)
        sys.exit(0)

    if args.dry_run:
        print("Dry run: would update the following SKU -> cost:")
        for sku, cost in rows:
            print(f"  {sku} -> {cost}")
        return

    token = load_token()
    headers = build_headers(token)
    session = requests.Session()

    if args.debug:
        resp = graphql_request(
            session, headers, QUERY_PRODUCTS_PAGE, {"first": 10, "after": None}
        )
        check_fatal_status(resp)
        print(json.dumps(resp.json(), indent=2))
        return

    updated = 0
    for sku, cost in rows:
        try:
            node_id = find_id_by_sku(session, headers, sku)
            time.sleep(RATE_LIMIT_SLEEP_SEC)
            if node_id is None:
                warn_sku_not_found(sku)
                continue
            ok = update_unit_cost(session, headers, node_id, cost)
            time.sleep(RATE_LIMIT_SLEEP_SEC)
            if not ok:
                warn_sku_not_found(sku)
            else:
                updated += 1
        except Exception as e:
            warn_sku_not_found(sku)
            print(f"  ({e})", file=sys.stderr)
    if updated:
        print(f"Updated {updated} product(s) with new unit costs.")


if __name__ == "__main__":
    main()
