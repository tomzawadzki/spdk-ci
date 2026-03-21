"""Tests for github_client.py — thin wrapper around common.github_api."""

from unittest.mock import patch, MagicMock

import requests

import github_client
from config import config as checks_config


def _mock_response(status_code=200, json_data=None, ok=True, text=""):
    """Build a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = ok
    resp.text = text
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# trigger_workflow
# ---------------------------------------------------------------------------

@patch("common.github_api.request_with_retry")
def test_trigger_workflow(mock_retry, monkeypatch):
    """Verify correct URL and payload for trigger_workflow."""
    monkeypatch.setattr(checks_config, "github_repo", "myorg/myrepo")
    monkeypatch.setattr(checks_config, "github_token", "tok123")
    mock_retry.return_value = _mock_response(204)

    github_client.trigger_workflow("per-patch-event", {"change": {"number": 1}})

    mock_retry.assert_called_once()
    args, kwargs = mock_retry.call_args
    assert args[0] == "POST"
    assert args[1] == "https://api.github.com/repos/myorg/myrepo/dispatches"
    assert kwargs["json"]["event_type"] == "per-patch-event"
    assert kwargs["json"]["client_payload"] == {"change": {"number": 1}}


# ---------------------------------------------------------------------------
# rerun_failed_jobs
# ---------------------------------------------------------------------------

@patch("common.github_api.request_with_retry")
def test_rerun_failed_jobs(mock_retry, monkeypatch):
    """Verify correct URL for rerun_failed_jobs."""
    monkeypatch.setattr(checks_config, "github_repo", "myorg/myrepo")
    monkeypatch.setattr(checks_config, "github_token", "tok123")
    mock_retry.return_value = _mock_response(201)

    github_client.rerun_failed_jobs(42)

    mock_retry.assert_called_once()
    args, kwargs = mock_retry.call_args
    assert args[0] == "POST"
    assert "actions/runs/42/rerun-failed-jobs" in args[1]


# ---------------------------------------------------------------------------
# get_workflow_run
# ---------------------------------------------------------------------------

@patch("common.github_api.request_with_retry")
def test_get_workflow_run(mock_retry, monkeypatch):
    """Verify response parsing for get_workflow_run."""
    monkeypatch.setattr(checks_config, "github_repo", "myorg/myrepo")
    monkeypatch.setattr(checks_config, "github_token", "tok123")
    mock_retry.return_value = _mock_response(200, json_data={"id": 55, "status": "completed"})

    result = github_client.get_workflow_run(55)

    assert result == {"id": 55, "status": "completed"}
    args, _ = mock_retry.call_args
    assert "actions/runs/55" in args[1]
