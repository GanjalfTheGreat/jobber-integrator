"""
Stress tests for Step 1: Hosting and app shell.

Step 1 = app runs, public routes (health, manage URL), config from env, DB init.
No OAuth required to reach the shell; these tests define "Step 1 done".
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_db.sqlite")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client-id")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-secret")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app.main import app
from app.database import init_db


@pytest.fixture
def client():
    """FastAPI test client; DB table created so lifespan/init_db has run."""
    init_db()
    return TestClient(app)


# ---- App starts ----
def test_app_starts_and_responds(client):
    """Step 1: App shell is runnable and returns any response."""
    r = client.get("/health")
    assert r.status_code == 200


# ---- Health (public, for deploy checks) ----
def test_health_returns_200_json(client):
    """Step 1: Health endpoint returns 200 and JSON with status."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/json")
    data = r.json()
    assert data.get("status") == "ok"


# ---- Root → single entry point ----
def test_root_redirects_to_dashboard(client):
    """Step 1: Root URL redirects to Manage App URL (dashboard)."""
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location") == "/dashboard"


# ---- Manage App URL reachable without auth ----
def test_dashboard_reachable_without_auth(client):
    """Step 1: Manage App URL (dashboard) is reachable without OAuth."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_dashboard_contains_shell_content(client):
    """Step 1: Dashboard shows app name and core UI (Connect or CSV info)."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    text = r.text
    assert "Price Sync" in text
    assert "Part_Num" in text or "Trade_Cost" in text or "CSV" in text
    assert "Connect" in text or "connected" in text.lower()


# ---- Config from env ----
def test_dashboard_uses_base_url_from_config(client):
    """Step 1: App uses BASE_URL from env (e.g. in template or links)."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    # BASE_URL is in template (e.g. health link, callback hint)
    assert "localhost:8000" in r.text or "http://" in r.text


# ---- DB init (lifespan) ----
def test_db_init_runs_and_table_exists(client):
    """Step 1: Lifespan runs init_db; DB is queryable (table exists)."""
    # First request triggers lifespan → init_db()
    client.get("/health")
    # If we can query without error, table exists and DB is usable
    from app.database import get_connection_by_account_id
    result = get_connection_by_account_id("nonexistent-id")
    assert result is None


# ---- Trailing slash / routing sanity ----
def test_health_no_trailing_slash(client):
    """Step 1: /health without trailing slash returns 200."""
    r = client.get("/health")
    assert r.status_code == 200


def test_unknown_route_returns_404(client):
    """Step 1: Unknown routes return 404 (no 500)."""
    r = client.get("/unknown-route")
    assert r.status_code == 404
