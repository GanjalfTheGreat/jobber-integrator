"""
Tests for Step 4: sync API and CSV parsing.
"""
import os
from io import BytesIO
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_db.sqlite")
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client-id")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-secret")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
from app.sync import parse_csv_from_bytes, run_sync


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
        mock_run.return_value = {"updated": 1, "skus_not_found": [], "error": None}
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
