"""
Tests for Step 4: sync API and CSV parsing. Includes Enhancement 1 (match by code then name).
"""
import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_db.sqlite")
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client-id")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-secret")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
from app.sync import (
    parse_csv_from_bytes,
    run_sync,
    run_sync_preview,
    _probe_code_available,
    _find_id_by_sku,
)


@pytest.fixture
def client():
    init_db()
    return TestClient(app)


def test_parse_csv_from_bytes_valid():
    """Step 4: parse CSV with Part_Num and Trade_Cost."""
    csv = b"Part_Num,Trade_Cost\nSKU1,10.50\nSKU2,20"
    rows = parse_csv_from_bytes(csv)
    assert rows == [("SKU1", 10.5), ("SKU2", 20.0)]


def test_parse_csv_from_bytes_utf8_bom():
    """Step 4: parse CSV with UTF-8 BOM."""
    csv = "\ufeffPart_Num,Trade_Cost\nA,1.0".encode("utf-8")
    rows = parse_csv_from_bytes(csv)
    assert rows == [("A", 1.0)]


def test_parse_csv_from_bytes_missing_columns_raises():
    """Step 4: missing required columns raises ValueError."""
    csv = b"Name,Price\nx,1"
    with pytest.raises(ValueError, match="Part_Num and Trade_Cost"):
        parse_csv_from_bytes(csv)


def test_parse_csv_from_bytes_no_valid_rows_raises():
    """Step 4: no valid rows raises ValueError."""
    csv = b"Part_Num,Trade_Cost\n,"
    with pytest.raises(ValueError, match="No valid rows"):
        parse_csv_from_bytes(csv)


def test_api_sync_requires_auth(client):
    """Step 4: POST /api/sync without session returns 403."""
    response = client.post("/api/sync", files={"file": ("test.csv", b"Part_Num,Trade_Cost\nx,1")})
    assert response.status_code == 403
    assert "error" in response.json()
    assert "connect" in response.json()["error"].lower()


def test_api_sync_requires_csv(client):
    """Step 4: POST /api/sync with non-CSV returns 400."""
    from app.cookies import make_account_cookie_value
    cookie = make_account_cookie_value("acc-123")
    # No connection in DB, but we're testing the file type check first
    response = client.post(
        "/api/sync",
        files={"file": ("data.txt", b"not csv")},
        cookies={"price_sync_account": cookie},
    )
    # May be 400 (bad file type) or 403 (not connected)
    assert response.status_code in (400, 403)


def test_api_sync_bad_csv_returns_400(client):
    """Step 4: POST /api/sync with invalid CSV (wrong columns) returns 400."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-sync", "Test", "at", "rt")
    cookie = make_account_cookie_value("acc-sync")
    response = client.post(
        "/api/sync",
        files={"file": ("bad.csv", b"Name,Price\nx,1")},
        cookies={"price_sync_account": cookie},
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_api_sync_success_returns_result(client):
    """Step 4: POST /api/sync when connected returns updated + skus_not_found."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-sync-ok", "Test", "at", "rt")
    cookie = make_account_cookie_value("acc-sync-ok")
    csv_content = b"Part_Num,Trade_Cost\nProductA,99.99"
    with patch("app.main.run_sync") as mock_run:
        mock_run.return_value = {"updated": 1, "skus_not_found": [], "skipped_protected": 0, "error": None}
        response = client.post(
            "/api/sync",
            files={"file": ("prices.csv", csv_content)},
            cookies={"price_sync_account": cookie},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["updated"] == 1
    assert data["skus_not_found"] == []
    assert data["error"] is None
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0]
    assert call_args[0] == "acc-sync-ok"
    assert call_args[1] == [("ProductA", 99.99)]


def test_run_sync_no_connection_returns_error():
    """Step 4: run_sync with no DB connection returns error in result."""
    init_db()
    result = run_sync("nonexistent-account", [("SKU1", 10.0)])
    assert result["error"] is not None
    assert "connect" in result["error"].lower()
    assert result["updated"] == 0
    assert result["skus_not_found"] == []
    assert result.get("skipped_protected", 0) == 0


# ---- Enhancement 1: match by code (SKU) then name ----
def test_probe_code_available_true_when_no_errors():
    """Enhancement 1: _probe_code_available returns True when GraphQL response has no errors."""
    session = MagicMock()
    headers = {}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"productOrServices": {"nodes": [{"id": "1", "name": "x"}]}}}
    with patch("app.sync._graphql_request", return_value=mock_resp):
        assert _probe_code_available(session, headers) is True


def test_probe_code_available_false_when_errors():
    """Enhancement 1: _probe_code_available returns False when GraphQL returns errors (e.g. code field missing)."""
    session = MagicMock()
    headers = {}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"errors": [{"message": "Field 'code' doesn't exist"}]}
    with patch("app.sync._graphql_request", return_value=mock_resp):
        assert _probe_code_available(session, headers) is False


def test_find_id_by_sku_name_only_matches_name():
    """Enhancement 1: match_by_code_first=False matches by name only. Returns (id, current_cost)."""
    session = MagicMock()
    headers = {}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "productOrServices": {
                "nodes": [{"id": "enc-123", "name": "ProductA", "internalUnitCost": 10.0}],
                "pageInfo": {"hasNextPage": False},
            }
        }
    }
    with patch("app.sync._graphql_request", return_value=mock_resp):
        assert _find_id_by_sku(session, headers, "ProductA", match_by_code_first=False) == ("enc-123", 10.0)
        assert _find_id_by_sku(session, headers, "Other", match_by_code_first=False) == (None, None)


def test_find_id_by_sku_code_first_matches_code_then_name():
    """Enhancement 1: match_by_code_first=True matches code first, then name. Returns (id, current_cost)."""
    session = MagicMock()
    headers = {}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "productOrServices": {
                "nodes": [
                    {"id": "enc-by-code", "name": "15mm Copper Tube 3m", "code": "COP15-3M", "internalUnitCost": 8.45},
                    {"id": "enc-by-name", "name": "NoCodeProduct", "code": None, "internalUnitCost": None},
                ],
                "pageInfo": {"hasNextPage": False},
            }
        }
    }
    with patch("app.sync._graphql_request", return_value=mock_resp):
        assert _find_id_by_sku(session, headers, "COP15-3M", match_by_code_first=True) == ("enc-by-code", 8.45)
        assert _find_id_by_sku(session, headers, "NoCodeProduct", match_by_code_first=True) == ("enc-by-name", None)
        assert _find_id_by_sku(session, headers, "NotFound", match_by_code_first=True) == (None, None)


# ---- Enhancement 2: price protection (only update if new cost higher) ----
def test_run_sync_only_increase_cost_skips_when_new_lower_or_equal():
    """Enhancement 2: when only_increase_cost=True, skip update when new cost <= current; count skipped_protected."""
    from app.database import save_connection
    init_db()
    save_connection("acc-e2", "E2", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku") as mock_find:
            with patch("app.sync._update_unit_cost") as mock_update:
                mock_find.return_value = ("id-1", 10.0)  # current cost 10
                result = run_sync("acc-e2", [("SKU1", 5.0), ("SKU2", 10.0)], only_increase_cost=True)
    assert result["updated"] == 0
    assert result["skipped_protected"] == 2  # 5 <= 10, 10 <= 10
    mock_update.assert_not_called()


def test_run_sync_only_increase_cost_updates_when_new_higher():
    """Enhancement 2: when only_increase_cost=True, update when new cost > current."""
    from app.database import save_connection
    init_db()
    save_connection("acc-e2b", "E2b", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku") as mock_find:
            with patch("app.sync._update_unit_cost", return_value=True) as mock_update:
                mock_find.return_value = ("id-1", 10.0)
                result = run_sync("acc-e2b", [("SKU1", 15.0)], only_increase_cost=True)
    assert result["updated"] == 1
    assert result["skipped_protected"] == 0
    mock_update.assert_called_once()
    assert mock_update.call_args[0][2] == "id-1" and mock_update.call_args[0][3] == 15.0


def test_api_sync_accepts_only_increase_cost_and_returns_skipped_protected(client):
    """Enhancement 2: POST /api/sync with only_increase_cost returns skipped_protected in JSON."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-e2-api", "E2", "at", "rt")
    cookie = make_account_cookie_value("acc-e2-api")
    with patch("app.main.run_sync") as mock_run:
        mock_run.return_value = {"updated": 0, "skus_not_found": [], "skipped_protected": 3, "error": None}
        r = client.post(
            "/api/sync",
            data={"only_increase_cost": "true"},
            files={"file": ("p.csv", b"Part_Num,Trade_Cost\nA,1")},
            cookies={"price_sync_account": cookie},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["skipped_protected"] == 3
    mock_run.assert_called_once()
    assert mock_run.call_args[0][2] is True  # only_increase_cost


# ---- Enhancement 3: preview (dry-run, no writes) ----
def test_run_sync_preview_returns_increases_decreases_unchanged():
    """Enhancement 3: run_sync_preview returns counts; no mutations."""
    from app.database import save_connection
    init_db()
    save_connection("acc-prev", "Prev", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku") as mock_find:
            mock_find.side_effect = [
                ("id-1", 5.0),   # cost 10 > 5 -> increase
                ("id-2", 20.0),  # cost 15 < 20 -> decrease
                ("id-3", 7.0),   # cost 7 == 7 -> unchanged
                (None, None),    # not found
            ]
            result = run_sync_preview("acc-prev", [
                ("A", 10.0),
                ("B", 15.0),
                ("C", 7.0),
                ("D", 1.0),
            ])
    assert result["increases"] == 1
    assert result["decreases"] == 1
    assert result["unchanged"] == 1
    assert result["skus_not_found"] == ["D"]
    assert result["error"] is None


def test_run_sync_preview_no_connection_returns_error():
    """Enhancement 3: run_sync_preview with no account returns error."""
    init_db()
    result = run_sync_preview("nonexistent", [("X", 1.0)])
    assert result["error"] is not None
    assert result["increases"] == 0
    assert result["decreases"] == 0
    assert result["unchanged"] == 0


def test_api_sync_preview_requires_auth(client):
    """Enhancement 3: POST /api/sync/preview without session returns 403."""
    r = client.post("/api/sync/preview", files={"file": ("p.csv", b"Part_Num,Trade_Cost\nA,1")})
    assert r.status_code == 403
    assert "error" in r.json()


def test_api_sync_preview_returns_counts(client):
    """Enhancement 3: POST /api/sync/preview returns increases, decreases, unchanged, skus_not_found."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-prev-api", "Prev", "at", "rt")
    cookie = make_account_cookie_value("acc-prev-api")
    with patch("app.main.run_sync_preview") as mock_preview:
        mock_preview.return_value = {"increases": 2, "decreases": 1, "unchanged": 0, "skus_not_found": ["Z"], "error": None}
        r = client.post(
            "/api/sync/preview",
            files={"file": ("p.csv", b"Part_Num,Trade_Cost\nA,1\nB,2\nC,3")},
            cookies={"price_sync_account": cookie},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["increases"] == 2
    assert data["decreases"] == 1
    assert data["unchanged"] == 0
    assert data["skus_not_found"] == ["Z"]
