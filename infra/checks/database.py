"""SQLite schema and operations."""

import logging
import os
import sqlite3
from contextlib import contextmanager

from config import config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gerrit_change_number INTEGER NOT NULL,
    gerrit_patchset_number INTEGER NOT NULL,
    gerrit_project TEXT DEFAULT '',
    github_run_id INTEGER UNIQUE NOT NULL,
    github_run_number INTEGER,
    github_run_attempt INTEGER DEFAULT 1,
    workflow_name TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    conclusion TEXT,
    html_url TEXT,
    event_type TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workflow_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_run_id INTEGER NOT NULL,
    github_job_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    conclusion TEXT,
    html_url TEXT,
    started_at TEXT,
    completed_at TEXT,
    runner_name TEXT,
    FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_change ON workflow_runs(gerrit_change_number, gerrit_patchset_number);
CREATE INDEX IF NOT EXISTS idx_runs_github ON workflow_runs(github_run_id);
CREATE INDEX IF NOT EXISTS idx_jobs_run ON workflow_jobs(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_jobs_github ON workflow_jobs(github_job_id);
"""


def init_db():
    """Initialize the database schema and enable WAL mode."""
    os.makedirs(os.path.dirname(config.database_path) or ".", exist_ok=True)
    with get_db() as db:
        db.executescript("PRAGMA journal_mode=WAL;" + SCHEMA)
    logger.info("Database initialized at %s", config.database_path)


@contextmanager
def get_db():
    """Yield a database connection with row_factory set."""
    conn = sqlite3.connect(config.database_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- Workflow Runs ---

def upsert_workflow_run(*, gerrit_change_number, gerrit_patchset_number,
                        github_run_id, gerrit_project="", github_run_number=None,
                        github_run_attempt=1, workflow_name=None, status="queued",
                        conclusion=None, html_url=None, event_type=None):
    """Insert or update a workflow run."""
    with get_db() as db:
        db.execute("""
            INSERT INTO workflow_runs (
                gerrit_change_number, gerrit_patchset_number, gerrit_project,
                github_run_id, github_run_number, github_run_attempt,
                workflow_name, status, conclusion, html_url, event_type,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(github_run_id) DO UPDATE SET
                status = excluded.status,
                conclusion = excluded.conclusion,
                html_url = excluded.html_url,
                github_run_number = COALESCE(excluded.github_run_number, github_run_number),
                github_run_attempt = COALESCE(excluded.github_run_attempt, github_run_attempt),
                workflow_name = COALESCE(excluded.workflow_name, workflow_name),
                event_type = COALESCE(excluded.event_type, event_type),
                gerrit_project = CASE WHEN excluded.gerrit_project != '' THEN excluded.gerrit_project ELSE gerrit_project END,
                updated_at = datetime('now')
        """, (gerrit_change_number, gerrit_patchset_number, gerrit_project,
              github_run_id, github_run_number, github_run_attempt,
              workflow_name, status, conclusion, html_url, event_type))
    logger.info("Upserted workflow run %d (status=%s)", github_run_id, status)


def get_runs_for_change(change_number, patchset_number):
    """Get all workflow runs and their jobs for a change/patchset."""
    with get_db() as db:
        runs = db.execute("""
            SELECT * FROM workflow_runs
            WHERE gerrit_change_number = ? AND gerrit_patchset_number = ?
            ORDER BY created_at DESC
        """, (change_number, patchset_number)).fetchall()

        result = []
        for run in runs:
            jobs = db.execute("""
                SELECT * FROM workflow_jobs
                WHERE workflow_run_id = ?
                ORDER BY name
            """, (run["id"],)).fetchall()

            result.append({
                "github_run_id": run["github_run_id"],
                "workflow_name": run["workflow_name"],
                "status": run["status"],
                "conclusion": run["conclusion"],
                "html_url": run["html_url"],
                "attempt": run["github_run_attempt"],
                "created_at": run["created_at"],
                "updated_at": run["updated_at"],
                "jobs": [
                    {
                        "github_job_id": job["github_job_id"],
                        "name": job["name"],
                        "status": job["status"],
                        "conclusion": job["conclusion"],
                        "html_url": job["html_url"],
                        "started_at": job["started_at"],
                        "completed_at": job["completed_at"],
                    }
                    for job in jobs
                ],
            })
        return result


def get_latest_run_for_change(change_number, patchset_number):
    """Get the most recent workflow run for a change/patchset."""
    with get_db() as db:
        row = db.execute("""
            SELECT * FROM workflow_runs
            WHERE gerrit_change_number = ? AND gerrit_patchset_number = ?
            ORDER BY created_at DESC LIMIT 1
        """, (change_number, patchset_number)).fetchone()
        return dict(row) if row else None


def get_run_by_github_id(github_run_id):
    """Look up a workflow run by its GitHub run ID."""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM workflow_runs WHERE github_run_id = ?",
            (github_run_id,),
        ).fetchone()
        return dict(row) if row else None


# --- Workflow Jobs ---

def upsert_workflow_job(*, workflow_run_id, github_job_id, name, status="queued",
                        conclusion=None, html_url=None, started_at=None,
                        completed_at=None, runner_name=None):
    """Insert or update a workflow job."""
    with get_db() as db:
        db.execute("""
            INSERT INTO workflow_jobs (
                workflow_run_id, github_job_id, name, status, conclusion,
                html_url, started_at, completed_at, runner_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(github_job_id) DO UPDATE SET
                name = excluded.name,
                status = excluded.status,
                conclusion = excluded.conclusion,
                html_url = excluded.html_url,
                started_at = COALESCE(excluded.started_at, started_at),
                completed_at = excluded.completed_at,
                runner_name = COALESCE(excluded.runner_name, runner_name)
        """, (workflow_run_id, github_job_id, name, status, conclusion,
              html_url, started_at, completed_at, runner_name))
    logger.info("Upserted job %d (%s, status=%s)", github_job_id, name, status)


# --- Cleanup ---

def cleanup_old_runs(days: int = 30):
    """Delete workflow runs older than the given number of days."""
    with get_db() as db:
        cursor = db.execute("""
            DELETE FROM workflow_runs
            WHERE created_at < datetime('now', ? || ' days')
        """, (f"-{days}",))
        if cursor.rowcount:
            logger.info("Cleaned up %d old workflow runs", cursor.rowcount)
