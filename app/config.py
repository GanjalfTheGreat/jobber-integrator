"""
App configuration from environment. Used by the web app (OAuth, DB, base URL).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (parent of app/)
_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / ".env")


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


# Jobber OAuth (from Developer Center app settings)
JOBBER_CLIENT_ID = _get("JOBBER_CLIENT_ID")
JOBBER_CLIENT_SECRET = _get("JOBBER_CLIENT_SECRET")

# Base URL of this app (no trailing slash). For local dev: http://localhost:8000
# For production: https://yourapp.com
BASE_URL = _get("BASE_URL", "http://localhost:8000")

# SQLite by default; override with DATABASE_URL for Postgres etc. later
DATABASE_URL = _get("DATABASE_URL", "sqlite:///./app.db")
PROJECT_ROOT = _root

# Optional: secret for signing session cookies (generate a random string in production)
SECRET_KEY = _get("SECRET_KEY", "dev-secret-change-in-production")
