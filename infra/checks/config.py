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
    queue_process_interval: int = 60
    max_running_workflows: int = 3
    recovery_window_days: int = 7
    gerrit_query_limit: int = 300

    def __post_init__(self):
        self.github_token = os.getenv("CHECKS_GITHUB_TOKEN", self.github_token)
        self.github_webhook_secret = os.getenv("CHECKS_GITHUB_WEBHOOK_SECRET", self.github_webhook_secret)
        self.github_repo = os.getenv("CHECKS_GITHUB_REPO", self.github_repo)
        self.gerrit_url = os.getenv("GERRIT_URL", self.gerrit_url).rstrip("/")
        self.database_path = os.getenv("CHECKS_DATABASE_PATH", self.database_path)
        self.api_key = os.getenv("CHECKS_API_KEY", self.api_key)
        self.log_level = os.getenv("LOG_LEVEL", self.log_level).upper()
        origins = os.getenv("CHECKS_CORS_ORIGINS", "")
        if origins:
            self.cors_origins = [o.strip() for o in origins.split(",") if o.strip()]

        for attr in ["queue_process_interval", "max_running_workflows",
                     "recovery_window_days", "gerrit_query_limit"]:
            env_val = os.getenv(f"CHECKS_{attr.upper()}")
            if env_val is not None:
                try:
                    setattr(self, attr, int(env_val))
                except ValueError:
                    logger.warning("CHECKS_%s must be an integer, keeping default %s",
                                   attr.upper(), getattr(self, attr))

        if not self.github_token:
            logger.warning("CHECKS_GITHUB_TOKEN not set — trigger/rerun will fail")


config = ChecksConfig()
