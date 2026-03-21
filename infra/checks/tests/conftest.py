"""Shared pytest fixtures for the checks backend tests."""

import sys
import os

# Add the checks directory to sys.path so we can import the flat modules.
CHECKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CHECKS_DIR not in sys.path:
    sys.path.insert(0, CHECKS_DIR)

import pytest

# Import after path setup
from config import config as checks_config
import database


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary SQLite database for each test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(checks_config, "database_path", db_path)
    database.init_db()
    return db_path


@pytest.fixture()
def client():
    """Return a FastAPI TestClient for the app."""
    from fastapi.testclient import TestClient
    from app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
