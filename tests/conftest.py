"""
Pytest fixtures for Step 2 (OAuth) tests. Sets test env before app is loaded.
"""
import os
import sys

# File-based DB so all connections share the same DB (sqlite :memory: is per-connection)
os.environ["DATABASE_URL"] = "sqlite:///./test_db.sqlite"
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client-id")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

# Ensure app is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


