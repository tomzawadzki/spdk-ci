"""Tests for webhook_handler.py — signature validation and event processing."""

import hashlib
import hmac

import database
import webhook_handler
from config import config as checks_config


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _make_signature(payload: bytes, secret: str) -> str:
    """Create a valid sha256 HMAC signature header."""
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_signature_valid(monkeypatch):
    """Correct HMAC passes verification."""
    monkeypatch.setattr(checks_config, "github_webhook_secret", "testsecret")
    payload = b'{"action":"completed"}'
    sig = _make_signature(payload, "testsecret")

    assert webhook_handler.verify_signature(payload, sig) is True


def test_verify_signature_invalid(monkeypatch):
    """Wrong signature fails verification."""
    monkeypatch.setattr(checks_config, "github_webhook_secret", "testsecret")
    payload = b'{"action":"completed"}'
    sig = _make_signature(payload, "wrongsecret")

    assert webhook_handler.verify_signature(payload, sig) is False


def test_verify_signature_no_secret(monkeypatch):
    """When webhook secret is not configured, all signatures pass."""
    monkeypatch.setattr(checks_config, "github_webhook_secret", "")

    assert webhook_handler.verify_signature(b"anything", "sha256=abc") is True
    assert webhook_handler.verify_signature(b"anything", None) is True


def test_verify_signature_missing_header(monkeypatch):
    """No signature header fails when secret is configured."""
    monkeypatch.setattr(checks_config, "github_webhook_secret", "secret")

    assert webhook_handler.verify_signature(b"data", None) is False


def test_verify_signature_wrong_prefix(monkeypatch):
    """sha1= prefix (instead of sha256=) fails."""
    monkeypatch.setattr(checks_config, "github_webhook_secret", "secret")
    payload = b"data"
    digest = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()

    assert webhook_handler.verify_signature(payload, f"sha1={digest}") is False


# ---------------------------------------------------------------------------
# handle_workflow_run
# ---------------------------------------------------------------------------

def _run_payload(action, github_run_id, *, name="CI", status="queued",
                 conclusion=None, html_url=None, run_number=1, run_attempt=1,
                 display_title="", head_branch="", head_commit_message="",
                 event="repository_dispatch"):
    """Build a workflow_run webhook payload."""
    return {
        "action": action,
        "workflow_run": {
            "id": github_run_id,
            "name": name,
            "status": status,
            "conclusion": conclusion,
            "html_url": html_url or f"https://github.com/runs/{github_run_id}",
            "run_number": run_number,
            "run_attempt": run_attempt,
            "display_title": display_title,
            "head_branch": head_branch,
            "head_commit": {"message": head_commit_message} if head_commit_message else None,
            "event": event,
        },
    }


def test_handle_workflow_run_requested():
    """New run creates a DB record when Gerrit info is in the ref."""
    # Pre-register the run so the handler can find Gerrit info
    database.upsert_workflow_run(
        gerrit_change_number=12345,
        gerrit_patchset_number=5,
        github_run_id=2001,
    )

    payload = _run_payload(
        "requested", 2001, status="queued",
        head_branch="refs/changes/45/12345/5",
    )
    webhook_handler.handle_workflow_run(payload)

    run = database.get_run_by_github_id(2001)
    assert run is not None
    assert run["gerrit_change_number"] == 12345
    assert run["status"] == "queued"


def test_handle_workflow_run_completed():
    """Completed event updates status and conclusion."""
    database.upsert_workflow_run(
        gerrit_change_number=12345,
        gerrit_patchset_number=5,
        github_run_id=2002,
        status="in_progress",
    )

    payload = _run_payload(
        "completed", 2002, status="completed", conclusion="success",
    )
    webhook_handler.handle_workflow_run(payload)

    run = database.get_run_by_github_id(2002)
    assert run["status"] == "completed"
    assert run["conclusion"] == "success"


def test_handle_workflow_run_missing_id():
    """Gracefully ignores payloads without a run ID."""
    payload = {"action": "requested", "workflow_run": {}}
    # Should not raise
    webhook_handler.handle_workflow_run(payload)


def test_handle_workflow_run_unknown_change():
    """Logs warning when Gerrit info cannot be extracted (no pre-existing run)."""
    payload = _run_payload(
        "requested", 9999,
        display_title="Some random title",
        head_branch="main",
    )
    # No pre-existing run and no Gerrit info in payload → should not create a record
    webhook_handler.handle_workflow_run(payload)

    assert database.get_run_by_github_id(9999) is None


# ---------------------------------------------------------------------------
# handle_workflow_job
# ---------------------------------------------------------------------------

def _job_payload(action, github_job_id, github_run_id, *, name="build",
                 status="queued", conclusion=None, html_url=None,
                 started_at=None, completed_at=None, runner_name=None):
    """Build a workflow_job webhook payload."""
    return {
        "action": action,
        "workflow_job": {
            "id": github_job_id,
            "run_id": github_run_id,
            "name": name,
            "status": status,
            "conclusion": conclusion,
            "html_url": html_url or f"https://github.com/jobs/{github_job_id}",
            "started_at": started_at,
            "completed_at": completed_at,
            "runner_name": runner_name,
        },
    }


def test_handle_workflow_job_queued():
    """Creates a job record when the parent run exists."""
    database.upsert_workflow_run(
        gerrit_change_number=3000,
        gerrit_patchset_number=1,
        github_run_id=3001,
    )

    payload = _job_payload("queued", 30010, 3001, name="unit-tests")
    webhook_handler.handle_workflow_job(payload)

    runs = database.get_runs_for_change(3000, 1)
    assert len(runs) == 1
    assert len(runs[0]["jobs"]) == 1
    assert runs[0]["jobs"][0]["name"] == "unit-tests"
    assert runs[0]["jobs"][0]["status"] == "queued"


def test_handle_workflow_job_completed():
    """Updates job status, conclusion, and timestamps."""
    database.upsert_workflow_run(
        gerrit_change_number=3100,
        gerrit_patchset_number=1,
        github_run_id=3101,
    )
    webhook_handler.handle_workflow_job(
        _job_payload("queued", 31010, 3101, name="lint")
    )

    webhook_handler.handle_workflow_job(
        _job_payload(
            "completed", 31010, 3101,
            name="lint",
            status="completed",
            conclusion="failure",
            started_at="2024-01-01T00:00:00Z",
            completed_at="2024-01-01T00:05:00Z",
        )
    )

    runs = database.get_runs_for_change(3100, 1)
    job = runs[0]["jobs"][0]
    assert job["status"] == "completed"
    assert job["conclusion"] == "failure"
    assert job["completed_at"] == "2024-01-01T00:05:00Z"


def test_handle_workflow_job_no_run():
    """Ignores job when no parent run is tracked."""
    payload = _job_payload("queued", 99999, 88888, name="orphan-job")
    # Should not raise
    webhook_handler.handle_workflow_job(payload)

    # Verify no runs were created
    assert database.get_run_by_github_id(88888) is None


# ---------------------------------------------------------------------------
# _extract_gerrit_info
# ---------------------------------------------------------------------------

def test_extract_gerrit_info_from_ref():
    """Parses refs/changes/45/12345/5."""
    run = {"display_title": "", "head_branch": "refs/changes/45/12345/5"}
    change, patchset, project = webhook_handler._extract_gerrit_info(run)
    assert change == 12345
    assert patchset == 5
    assert project == ""


def test_extract_gerrit_info_from_text():
    """Parses 'Change 12345 Patchset 5' from display_title."""
    run = {
        "display_title": "Change 12345 Patchset 5",
        "head_branch": "main",
    }
    change, patchset, project = webhook_handler._extract_gerrit_info(run)
    assert change == 12345
    assert patchset == 5


def test_extract_gerrit_info_no_match():
    """Returns None, None, '' when no Gerrit info is found."""
    run = {
        "display_title": "Regular PR title",
        "head_branch": "feature-branch",
        "head_commit": {"message": "fix: some bug"},
    }
    change, patchset, project = webhook_handler._extract_gerrit_info(run)
    assert change is None
    assert patchset is None
    assert project == ""


def test_extract_gerrit_info_from_commit_message():
    """Parses Gerrit info from head_commit message."""
    run = {
        "display_title": "No info here",
        "head_branch": "main",
        "head_commit": {"message": "refs/changes/01/54321/12"},
    }
    change, patchset, project = webhook_handler._extract_gerrit_info(run)
    assert change == 54321
    assert patchset == 12


def test_extract_gerrit_info_change_variant():
    """Parses 'change/NN/CHANGE/PS' without refs/ prefix."""
    run = {
        "display_title": "",
        "head_branch": "changes/45/12345/7",
    }
    change, patchset, project = webhook_handler._extract_gerrit_info(run)
    assert change == 12345
    assert patchset == 7


# ---------------------------------------------------------------------------
# Verified vote on workflow_run completed
# ---------------------------------------------------------------------------

def test_workflow_run_completed_success_posts_verified_plus_one(monkeypatch):
    """workflow_run completed with success triggers Verified +1."""
    from unittest.mock import patch, call

    monkeypatch.setattr(checks_config, "gerrit_user", "ci-user")
    monkeypatch.setattr(checks_config, "gerrit_password", "ci-pass")

    database.upsert_workflow_run(
        gerrit_change_number=5000,
        gerrit_patchset_number=2,
        github_run_id=5001,
        status="in_progress",
    )

    payload = _run_payload(
        "completed", 5001, status="completed", conclusion="success",
    )

    with patch("webhook_handler.post_review") as mock_review:
        webhook_handler.handle_workflow_run(payload)
        mock_review.assert_called_once_with(
            gerrit_url=checks_config.gerrit_url,
            change_number=5000,
            patchset_number=2,
            label="Verified",
            value=1,
            message="Build Successful: all CI jobs passed.",
            username="ci-user",
            password="ci-pass",
        )


def test_workflow_run_completed_failure_posts_verified_minus_one(monkeypatch):
    """workflow_run completed with failure triggers Verified -1."""
    from unittest.mock import patch

    monkeypatch.setattr(checks_config, "gerrit_user", "ci-user")
    monkeypatch.setattr(checks_config, "gerrit_password", "ci-pass")

    database.upsert_workflow_run(
        gerrit_change_number=5100,
        gerrit_patchset_number=3,
        github_run_id=5101,
        status="in_progress",
    )

    payload = _run_payload(
        "completed", 5101, status="completed", conclusion="failure",
    )

    with patch("webhook_handler.post_review") as mock_review:
        webhook_handler.handle_workflow_run(payload)
        mock_review.assert_called_once_with(
            gerrit_url=checks_config.gerrit_url,
            change_number=5100,
            patchset_number=3,
            label="Verified",
            value=-1,
            message="Build Failed: one or more CI jobs failed.",
            username="ci-user",
            password="ci-pass",
        )


def test_workflow_run_completed_cancelled_no_vote(monkeypatch):
    """workflow_run completed with cancelled does NOT trigger a vote."""
    from unittest.mock import patch

    monkeypatch.setattr(checks_config, "gerrit_user", "ci-user")
    monkeypatch.setattr(checks_config, "gerrit_password", "ci-pass")

    database.upsert_workflow_run(
        gerrit_change_number=5200,
        gerrit_patchset_number=1,
        github_run_id=5201,
        status="in_progress",
    )

    payload = _run_payload(
        "completed", 5201, status="completed", conclusion="cancelled",
    )

    with patch("webhook_handler.post_review") as mock_review:
        webhook_handler.handle_workflow_run(payload)
        mock_review.assert_not_called()


def test_workflow_run_completed_no_gerrit_creds_no_vote(monkeypatch):
    """When Gerrit credentials are empty, no vote is posted."""
    from unittest.mock import patch

    monkeypatch.setattr(checks_config, "gerrit_user", "")
    monkeypatch.setattr(checks_config, "gerrit_password", "")

    database.upsert_workflow_run(
        gerrit_change_number=5300,
        gerrit_patchset_number=1,
        github_run_id=5301,
        status="in_progress",
    )

    payload = _run_payload(
        "completed", 5301, status="completed", conclusion="success",
    )

    with patch("webhook_handler.post_review") as mock_review:
        webhook_handler.handle_workflow_run(payload)
        mock_review.assert_not_called()
