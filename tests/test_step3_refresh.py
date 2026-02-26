"""
Tests for Step 3: token storage and refresh.
"""
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_db.sqlite")
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client-id")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-secret")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app.database import init_db, save_connection, get_connection_by_account_id, update_tokens
from app.jobber_oauth import refresh_access_token, get_valid_access_token


@pytest.fixture(autouse=True)
def _init_db():
    init_db()
    yield


def test_refresh_access_token_returns_new_tokens():
    """Step 3: refresh_access_token calls token endpoint and returns access_token, refresh_token."""
    with patch("app.jobber_oauth.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "access_token": "new_at",
            "refresh_token": "new_rt",
            "expires_in": 3600,
        }
        mock_post.return_value.raise_for_status = lambda: None
        result = refresh_access_token("old_rt")
    assert result["access_token"] == "new_at"
    assert result["refresh_token"] == "new_rt"
    assert result["expires_in"] == 3600
    call_kw = mock_post.call_args[1]
    assert call_kw["data"]["grant_type"] == "refresh_token"
    assert call_kw["data"]["refresh_token"] == "old_rt"


def test_get_valid_access_token_no_connection_raises():
    """Step 3: get_valid_access_token raises when no connection for account."""
    with pytest.raises(ValueError, match="No connection|reconnect"):
        get_valid_access_token("nonexistent-account")


def test_get_valid_access_token_returns_stored_token():
    """Step 3: get_valid_access_token returns current access_token when not expired."""
    save_connection(
        jobber_account_id="acc-123",
        jobber_account_name="Test",
        access_token="stored_at",
        refresh_token="stored_rt",
        expires_at=None,
    )
    token = get_valid_access_token("acc-123")
    assert token == "stored_at"


def test_get_valid_access_token_refreshes_when_expired():
    """Step 3: get_valid_access_token refreshes when expires_at is in the past."""
    import datetime
    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    save_connection(
        jobber_account_id="acc-expired",
        jobber_account_name="Test",
        access_token="old_at",
        refresh_token="old_rt",
        expires_at=past,
    )
    with patch("app.jobber_oauth.refresh_access_token") as mock_refresh:
        mock_refresh.return_value = {
            "access_token": "new_at",
            "refresh_token": "new_rt",
            "expires_in": 3600,
        }
        token = get_valid_access_token("acc-expired")
    assert token == "new_at"
    mock_refresh.assert_called_once_with("old_rt")
    # DB should be updated with new tokens
    row = get_connection_by_account_id("acc-expired")
    assert row["access_token"] == "new_at"
    assert row["refresh_token"] == "new_rt"


def test_update_tokens_updates_only_tokens():
    """Step 3: update_tokens updates access_token, refresh_token, expires_at; keeps account name."""
    save_connection(
        jobber_account_id="acc-update",
        jobber_account_name="Original Name",
        access_token="at1",
        refresh_token="rt1",
    )
    update_tokens("acc-update", "at2", "rt2", "2026-01-01T00:00:00Z")
    row = get_connection_by_account_id("acc-update")
    assert row["access_token"] == "at2"
    assert row["refresh_token"] == "rt2"
    assert row["jobber_account_name"] == "Original Name"
    assert row["access_token_expires_at"] == "2026-01-01T00:00:00Z"
