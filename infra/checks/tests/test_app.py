"""Tests for app.py — FastAPI endpoints."""

import hashlib
import hmac
import json
from unittest.mock import patch, MagicMock

import requests as http_requests

import database
from config import config as checks_config


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/checks-api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET runs
# ---------------------------------------------------------------------------

def test_get_runs_empty(client):
    resp = client.get("/checks-api/v1/changes/12345/patchsets/1/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_get_runs_with_data(client):
    """Returns runs with jobs after inserting data."""
    database.upsert_workflow_run(
        gerrit_change_number=100,
        gerrit_patchset_number=1,
        github_run_id=1000,
        workflow_name="CI",
        status="completed",
        conclusion="success",
    )
    run = database.get_run_by_github_id(1000)
    database.upsert_workflow_job(
        workflow_run_id=run["id"],
        github_job_id=10001,
        name="build",
        status="completed",
        conclusion="success",
    )

    resp = client.get("/checks-api/v1/changes/100/patchsets/1/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["runs"]) == 1
    assert data["runs"][0]["github_run_id"] == 1000
    assert len(data["runs"][0]["jobs"]) == 1
    assert data["runs"][0]["jobs"][0]["name"] == "build"


def test_get_runs_invalid_params(client):
    resp = client.get("/checks-api/v1/changes/-1/patchsets/1/runs")
    assert resp.status_code == 400

    resp = client.get("/checks-api/v1/changes/1/patchsets/0/runs")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST trigger
# ---------------------------------------------------------------------------

def _mock_gerrit_api():
    """Return a mock GerritRestAPI that reports a valid NEW change."""
    mock_gerrit = MagicMock()
    mock_gerrit.get.return_value = {
        "status": "NEW",
        "work_in_progress": False,
        "is_private": False,
        "revisions": {
            "abc123": {"_number": 3},
        },
        "labels": {},
    }
    return mock_gerrit


@patch("app.github_client.trigger_workflow")
@patch("app.GerritRestAPI")
def test_trigger_success(mock_gerrit_cls, mock_trigger, client, monkeypatch):
    monkeypatch.setattr(checks_config, "api_key", "")
    mock_gerrit_cls.return_value = _mock_gerrit_api()
    mock_trigger.return_value = MagicMock(status_code=204)

    resp = client.post("/checks-api/v1/changes/12345/patchsets/3/trigger")
    assert resp.status_code == 202
    data = resp.json()
    assert data["message"] == "Workflow triggered"
    assert data["change"] == 12345
    assert data["patchset"] == 3
    mock_trigger.assert_called_once()


@patch("app.GerritRestAPI")
def test_trigger_gerrit_wip(mock_gerrit_cls, client, monkeypatch):
    monkeypatch.setattr(checks_config, "api_key", "")
    mock_gerrit = MagicMock()
    mock_gerrit.get.return_value = {
        "status": "NEW",
        "work_in_progress": True,
        "is_private": False,
        "revisions": {"abc": {"_number": 1}},
        "labels": {},
    }
    mock_gerrit_cls.return_value = mock_gerrit

    resp = client.post("/checks-api/v1/changes/12345/patchsets/1/trigger")
    assert resp.status_code == 409
    assert "WIP" in resp.json()["detail"]


@patch("app.github_client.trigger_workflow")
@patch("app.GerritRestAPI")
def test_trigger_github_error(mock_gerrit_cls, mock_trigger, client, monkeypatch):
    monkeypatch.setattr(checks_config, "api_key", "")
    mock_gerrit_cls.return_value = _mock_gerrit_api()
    mock_trigger.side_effect = http_requests.HTTPError("502 Bad Gateway")

    resp = client.post("/checks-api/v1/changes/12345/patchsets/3/trigger")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# POST rerun
# ---------------------------------------------------------------------------

@patch("app.github_client.rerun_failed_jobs")
def test_rerun_success(mock_rerun, client, monkeypatch):
    monkeypatch.setattr(checks_config, "api_key", "")
    database.upsert_workflow_run(
        gerrit_change_number=200,
        gerrit_patchset_number=1,
        github_run_id=2000,
        status="completed",
        conclusion="failure",
    )
    mock_rerun.return_value = MagicMock(status_code=201)

    resp = client.post("/checks-api/v1/changes/200/patchsets/1/rerun")
    assert resp.status_code == 202
    data = resp.json()
    assert data["message"] == "Rerun triggered"
    assert data["github_run_id"] == 2000


def test_rerun_no_runs(client, monkeypatch):
    monkeypatch.setattr(checks_config, "api_key", "")
    resp = client.post("/checks-api/v1/changes/99999/patchsets/1/rerun")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST webhook
# ---------------------------------------------------------------------------

def test_webhook_workflow_run(client, monkeypatch):
    """POST webhook with valid payload creates/updates a run."""
    secret = "webhook-test-secret"
    monkeypatch.setattr(checks_config, "github_webhook_secret", secret)

    # Pre-register so the handler finds Gerrit info
    database.upsert_workflow_run(
        gerrit_change_number=5000,
        gerrit_patchset_number=1,
        github_run_id=50001,
    )

    payload = {
        "action": "completed",
        "workflow_run": {
            "id": 50001,
            "name": "CI",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/runs/50001",
            "run_number": 10,
            "run_attempt": 1,
            "display_title": "CI",
            "head_branch": "refs/changes/00/5000/1",
            "head_commit": None,
            "event": "repository_dispatch",
        },
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    resp = client.post(
        "/checks-api/v1/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "workflow_run",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200

    run = database.get_run_by_github_id(50001)
    assert run["status"] == "completed"
    assert run["conclusion"] == "success"


def test_webhook_invalid_signature(client, monkeypatch):
    monkeypatch.setattr(checks_config, "github_webhook_secret", "real-secret")
    body = b'{"action":"requested"}'
    resp = client.post(
        "/checks-api/v1/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=invalid",
            "X-GitHub-Event": "workflow_run",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST register
# ---------------------------------------------------------------------------

def test_register_run(client, monkeypatch):
    monkeypatch.setattr(checks_config, "api_key", "")
    resp = client.post(
        "/checks-api/v1/runs/register",
        json={
            "gerrit_change": 7000,
            "gerrit_patchset": 2,
            "gerrit_project": "spdk/spdk",
            "github_run_id": 70001,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["message"] == "Run registered"
    assert data["github_run_id"] == 70001

    # Verify it's in the database
    run = database.get_run_by_github_id(70001)
    assert run is not None
    assert run["gerrit_change_number"] == 7000
    assert run["gerrit_project"] == "spdk/spdk"


def test_register_run_invalid(client, monkeypatch):
    monkeypatch.setattr(checks_config, "api_key", "")
    resp = client.post(
        "/checks-api/v1/runs/register",
        json={
            "gerrit_change": -1,
            "gerrit_patchset": 1,
            "github_run_id": 1,
        },
    )
    assert resp.status_code == 400

    resp = client.post(
        "/checks-api/v1/runs/register",
        json={
            "gerrit_change": 1,
            "gerrit_patchset": 0,
            "github_run_id": 1,
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# API key enforcement
# ---------------------------------------------------------------------------

def test_trigger_requires_api_key(client, monkeypatch):
    """When api_key is configured, requests without it are rejected."""
    monkeypatch.setattr(checks_config, "api_key", "secret-key")
    resp = client.post("/checks-api/v1/changes/1/patchsets/1/trigger")
    assert resp.status_code == 401

    resp = client.post(
        "/checks-api/v1/changes/1/patchsets/1/trigger",
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401
