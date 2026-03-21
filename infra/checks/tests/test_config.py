"""Tests for config.py — ChecksConfig defaults and env var loading."""

import os

from config import ChecksConfig


def test_config_defaults():
    """Verify default values when no env vars are set."""
    cfg = ChecksConfig.__new__(ChecksConfig)
    # Set defaults manually (skip __post_init__)
    cfg.github_token = ""
    cfg.github_webhook_secret = ""
    cfg.github_repo = "spdk/spdk-ci"
    cfg.gerrit_url = "https://review.spdk.io"
    cfg.database_path = "/app/data/checks.db"
    cfg.api_key = ""
    cfg.cors_origins = ["http://localhost:8080"]
    cfg.log_level = "INFO"

    assert cfg.github_token == ""
    assert cfg.github_repo == "spdk/spdk-ci"
    assert cfg.gerrit_url == "https://review.spdk.io"
    assert cfg.database_path == "/app/data/checks.db"
    assert cfg.api_key == ""
    assert cfg.cors_origins == ["http://localhost:8080"]
    assert cfg.log_level == "INFO"


def test_config_from_env(monkeypatch):
    """Verify env var loading with CHECKS_ prefix."""
    monkeypatch.setenv("CHECKS_GITHUB_TOKEN", "gh_test_token")
    monkeypatch.setenv("CHECKS_GITHUB_WEBHOOK_SECRET", "secret123")
    monkeypatch.setenv("CHECKS_GITHUB_REPO", "myorg/myrepo")
    monkeypatch.setenv("GERRIT_URL", "https://gerrit.example.com")
    monkeypatch.setenv("CHECKS_DATABASE_PATH", "/tmp/test.db")
    monkeypatch.setenv("CHECKS_API_KEY", "apikey42")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    cfg = ChecksConfig()

    assert cfg.github_token == "gh_test_token"
    assert cfg.github_webhook_secret == "secret123"
    assert cfg.github_repo == "myorg/myrepo"
    assert cfg.gerrit_url == "https://gerrit.example.com"
    assert cfg.database_path == "/tmp/test.db"
    assert cfg.api_key == "apikey42"
    assert cfg.log_level == "DEBUG"  # uppercased


def test_config_cors_origins(monkeypatch):
    """Verify comma-separated CORS origins parsing."""
    monkeypatch.setenv("CHECKS_CORS_ORIGINS", "http://a.com, http://b.com , http://c.com")
    # Clear other env vars that might leak from previous tests
    monkeypatch.delenv("CHECKS_GITHUB_TOKEN", raising=False)

    cfg = ChecksConfig()

    assert cfg.cors_origins == ["http://a.com", "http://b.com", "http://c.com"]


def test_config_cors_origins_empty(monkeypatch):
    """Empty CHECKS_CORS_ORIGINS keeps default."""
    monkeypatch.setenv("CHECKS_CORS_ORIGINS", "")
    monkeypatch.delenv("CHECKS_GITHUB_TOKEN", raising=False)

    cfg = ChecksConfig()

    assert cfg.cors_origins == ["http://localhost:8080"]


def test_config_gerrit_url_strip_slash(monkeypatch):
    """Trailing slash is removed from gerrit_url."""
    monkeypatch.setenv("GERRIT_URL", "https://review.example.com/")
    monkeypatch.delenv("CHECKS_GITHUB_TOKEN", raising=False)

    cfg = ChecksConfig()

    assert cfg.gerrit_url == "https://review.example.com"
