"""Configuration for the checks-api service, loaded from environment."""

import os
import sys
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ChecksConfig:
    """Configuration for the checks-api service, loaded from environment."""
    github_token: str = ""
    github_webhook_secret: str = ""
    github_repo: str = "spdk/spdk-ci"
    gerrit_url: str = "https://review.spdk.io"
    database_path: str = "/app/data/checks.db"
    api_key: str = ""
    cors_origins: list = field(default_factory=lambda: ["http://localhost:8080"])
    log_level: str = "INFO"

    def __post_init__(self):
        self.github_token = os.getenv("GITHUB_TOKEN", self.github_token)
        self.github_webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET", self.github_webhook_secret)
        self.github_repo = os.getenv("GITHUB_REPO", self.github_repo)
        self.gerrit_url = os.getenv("GERRIT_URL", self.gerrit_url).rstrip("/")
        self.database_path = os.getenv("DATABASE_PATH", self.database_path)
        self.api_key = os.getenv("API_KEY", self.api_key)
        self.log_level = os.getenv("LOG_LEVEL", self.log_level).upper()
        origins = os.getenv("CORS_ORIGINS", "")
        if origins:
            self.cors_origins = [o.strip() for o in origins.split(",") if o.strip()]

        if not self.github_token:
            logger.warning("GITHUB_TOKEN not set — trigger/rerun will fail")


config = ChecksConfig()
