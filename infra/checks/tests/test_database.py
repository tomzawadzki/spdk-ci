"""Tests for database.py — schema, CRUD, and cleanup."""

import sqlite3

import database
from config import config as checks_config


def _connect():
    """Helper to get a raw connection to the test database."""
    conn = sqlite3.connect(checks_config.database_path)
    conn.row_factory = sqlite3.Row
    return conn


# --- Schema ---

def test_init_db():
    """Tables and indices exist after init_db()."""
    conn = _connect()
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    assert "workflow_runs" in tables
    assert "workflow_jobs" in tables


# --- Workflow runs ---

def test_upsert_workflow_run():
    """Insert a run and verify fields."""
    database.upsert_workflow_run(
        gerrit_change_number=12345,
        gerrit_patchset_number=3,
        github_run_id=100,
        gerrit_project="spdk/spdk",
        github_run_number=42,
        github_run_attempt=1,
        workflow_name="CI",
        status="in_progress",
        conclusion=None,
        html_url="https://github.com/runs/100",
        event_type="repository_dispatch",
    )

    run = database.get_run_by_github_id(100)
    assert run is not None
    assert run["gerrit_change_number"] == 12345
    assert run["gerrit_patchset_number"] == 3
    assert run["gerrit_project"] == "spdk/spdk"
    assert run["github_run_id"] == 100
    assert run["github_run_number"] == 42
    assert run["status"] == "in_progress"
    assert run["conclusion"] is None
    assert run["html_url"] == "https://github.com/runs/100"
    assert run["workflow_name"] == "CI"
    assert run["event_type"] == "repository_dispatch"


def test_upsert_workflow_run_update():
    """Update a run's status and conclusion via upsert."""
    database.upsert_workflow_run(
        gerrit_change_number=1,
        gerrit_patchset_number=1,
        github_run_id=200,
        status="queued",
    )

    database.upsert_workflow_run(
        gerrit_change_number=1,
        gerrit_patchset_number=1,
        github_run_id=200,
        status="completed",
        conclusion="success",
        html_url="https://github.com/runs/200",
    )

    run = database.get_run_by_github_id(200)
    assert run["status"] == "completed"
    assert run["conclusion"] == "success"
    assert run["html_url"] == "https://github.com/runs/200"


def test_upsert_workflow_run_preserves_fields():
    """COALESCE preserves existing non-null values when upsert supplies NULL."""
    database.upsert_workflow_run(
        gerrit_change_number=1,
        gerrit_patchset_number=1,
        github_run_id=300,
        workflow_name="CI Pipeline",
        github_run_number=10,
        event_type="push",
        gerrit_project="spdk/spdk",
    )

    # Upsert again with NULLs for optional fields
    database.upsert_workflow_run(
        gerrit_change_number=1,
        gerrit_patchset_number=1,
        github_run_id=300,
        status="completed",
        conclusion="failure",
        workflow_name=None,
        github_run_number=None,
        event_type=None,
        gerrit_project="",  # empty string should NOT overwrite
    )

    run = database.get_run_by_github_id(300)
    assert run["workflow_name"] == "CI Pipeline"  # preserved via COALESCE
    assert run["github_run_number"] == 10  # preserved
    assert run["event_type"] == "push"  # preserved
    assert run["gerrit_project"] == "spdk/spdk"  # preserved (empty excluded)


def test_get_runs_for_change():
    """Runs include nested job structures."""
    database.upsert_workflow_run(
        gerrit_change_number=500,
        gerrit_patchset_number=1,
        github_run_id=501,
        workflow_name="Build",
        status="completed",
        conclusion="success",
    )

    run = database.get_run_by_github_id(501)
    database.upsert_workflow_job(
        workflow_run_id=run["id"],
        github_job_id=5010,
        name="build-linux",
        status="completed",
        conclusion="success",
        html_url="https://github.com/jobs/5010",
    )
    database.upsert_workflow_job(
        workflow_run_id=run["id"],
        github_job_id=5011,
        name="test-unit",
        status="completed",
        conclusion="failure",
    )

    runs = database.get_runs_for_change(500, 1)
    assert len(runs) == 1

    r = runs[0]
    assert r["github_run_id"] == 501
    assert r["workflow_name"] == "Build"
    assert len(r["jobs"]) == 2

    job_names = {j["name"] for j in r["jobs"]}
    assert job_names == {"build-linux", "test-unit"}


def test_get_runs_for_change_empty():
    """No runs exist for a nonexistent change."""
    runs = database.get_runs_for_change(99999, 99)
    assert runs == []


def test_get_latest_run_for_change():
    """Returns the most recent run."""
    database.upsert_workflow_run(
        gerrit_change_number=600,
        gerrit_patchset_number=1,
        github_run_id=601,
        status="completed",
        conclusion="failure",
    )

    # Backdate the first run so the second is clearly newer
    conn = _connect()
    conn.execute(
        "UPDATE workflow_runs SET created_at = datetime('now', '-1 hour') WHERE github_run_id = 601"
    )
    conn.commit()
    conn.close()

    database.upsert_workflow_run(
        gerrit_change_number=600,
        gerrit_patchset_number=1,
        github_run_id=602,
        status="in_progress",
    )

    latest = database.get_latest_run_for_change(600, 1)
    assert latest is not None
    assert latest["github_run_id"] == 602


def test_get_run_by_github_id():
    """Lookup by GitHub run ID works."""
    database.upsert_workflow_run(
        gerrit_change_number=700,
        gerrit_patchset_number=2,
        github_run_id=701,
        workflow_name="Test",
    )

    run = database.get_run_by_github_id(701)
    assert run is not None
    assert run["gerrit_change_number"] == 700
    assert run["gerrit_patchset_number"] == 2

    assert database.get_run_by_github_id(999999) is None


# --- Workflow jobs ---

def test_upsert_workflow_job():
    """Insert a job and verify fields."""
    database.upsert_workflow_run(
        gerrit_change_number=800,
        gerrit_patchset_number=1,
        github_run_id=801,
    )
    run = database.get_run_by_github_id(801)

    database.upsert_workflow_job(
        workflow_run_id=run["id"],
        github_job_id=8010,
        name="lint",
        status="in_progress",
        html_url="https://github.com/jobs/8010",
        started_at="2024-01-01T00:00:00Z",
        runner_name="runner-1",
    )

    runs = database.get_runs_for_change(800, 1)
    jobs = runs[0]["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["github_job_id"] == 8010
    assert jobs[0]["name"] == "lint"
    assert jobs[0]["status"] == "in_progress"
    assert jobs[0]["html_url"] == "https://github.com/jobs/8010"
    assert jobs[0]["started_at"] == "2024-01-01T00:00:00Z"


def test_upsert_workflow_job_update():
    """Update a job's status and conclusion."""
    database.upsert_workflow_run(
        gerrit_change_number=900,
        gerrit_patchset_number=1,
        github_run_id=901,
    )
    run = database.get_run_by_github_id(901)

    database.upsert_workflow_job(
        workflow_run_id=run["id"],
        github_job_id=9010,
        name="build",
        status="queued",
    )

    database.upsert_workflow_job(
        workflow_run_id=run["id"],
        github_job_id=9010,
        name="build",
        status="completed",
        conclusion="success",
        completed_at="2024-01-01T01:00:00Z",
    )

    runs = database.get_runs_for_change(900, 1)
    job = runs[0]["jobs"][0]
    assert job["status"] == "completed"
    assert job["conclusion"] == "success"
    assert job["completed_at"] == "2024-01-01T01:00:00Z"


# --- Cleanup ---

def test_cleanup_old_runs():
    """Old runs beyond the retention window are deleted."""
    database.upsert_workflow_run(
        gerrit_change_number=1000,
        gerrit_patchset_number=1,
        github_run_id=1001,
    )

    # Manually backdate the created_at
    conn = _connect()
    conn.execute(
        "UPDATE workflow_runs SET created_at = datetime('now', '-60 days') WHERE github_run_id = 1001"
    )
    conn.commit()
    conn.close()

    database.cleanup_old_runs(days=30)
    assert database.get_run_by_github_id(1001) is None


def test_cleanup_old_runs_cascades():
    """FK CASCADE deletes associated jobs when the run is cleaned up."""
    database.upsert_workflow_run(
        gerrit_change_number=1100,
        gerrit_patchset_number=1,
        github_run_id=1101,
    )
    run = database.get_run_by_github_id(1101)
    database.upsert_workflow_job(
        workflow_run_id=run["id"],
        github_job_id=11010,
        name="job-a",
    )

    # Backdate
    conn = _connect()
    conn.execute(
        "UPDATE workflow_runs SET created_at = datetime('now', '-60 days') WHERE github_run_id = 1101"
    )
    conn.commit()
    conn.close()

    database.cleanup_old_runs(days=30)

    assert database.get_run_by_github_id(1101) is None
    # Verify the job is gone too
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM workflow_jobs WHERE github_job_id = 11010"
    ).fetchone()
    conn.close()
    assert row is None
