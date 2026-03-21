"""Tests for github_client.py — HTTP requests with retry logic."""

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

@patch("github_client._request_with_retry")
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

@patch("github_client._request_with_retry")
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

@patch("github_client._request_with_retry")
def test_get_workflow_run(mock_retry, monkeypatch):
    """Verify response parsing for get_workflow_run."""
    monkeypatch.setattr(checks_config, "github_repo", "myorg/myrepo")
    monkeypatch.setattr(checks_config, "github_token", "tok123")
    mock_retry.return_value = _mock_response(200, json_data={"id": 55, "status": "completed"})

    result = github_client.get_workflow_run(55)

    assert result == {"id": 55, "status": "completed"}
    args, _ = mock_retry.call_args
    assert "actions/runs/55" in args[1]


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

@patch("github_client.time.sleep")  # skip actual sleep
@patch("github_client.requests.request")
def test_retry_on_server_error(mock_request, mock_sleep, monkeypatch):
    """Retries on 500 status, then succeeds on 200."""
    monkeypatch.setattr(checks_config, "github_token", "tok123")

    error_resp = _mock_response(500, ok=False, text="Internal Server Error")
    ok_resp = _mock_response(200)
    mock_request.side_effect = [error_resp, ok_resp]

    result = github_client._request_with_retry("GET", "https://api.github.com/test")

    assert result.status_code == 200
    assert mock_request.call_count == 2
    mock_sleep.assert_called_once_with(1)  # 2**0 = 1


@patch("github_client.time.sleep")
@patch("github_client.requests.request")
def test_retry_on_connection_error(mock_request, mock_sleep, monkeypatch):
    """Retries on ConnectionError, then succeeds."""
    monkeypatch.setattr(checks_config, "github_token", "tok123")

    mock_request.side_effect = [
        requests.ConnectionError("Connection refused"),
        _mock_response(200),
    ]

    result = github_client._request_with_retry("GET", "https://api.github.com/test")

    assert result.status_code == 200
    assert mock_request.call_count == 2


@patch("github_client.time.sleep")
@patch("github_client.requests.request")
def test_retry_exhausted_raises(mock_request, mock_sleep, monkeypatch):
    """ConnectionError on all retries raises."""
    monkeypatch.setattr(checks_config, "github_token", "tok123")

    mock_request.side_effect = requests.ConnectionError("fail")

    try:
        github_client._request_with_retry("GET", "https://api.github.com/test", max_retries=3)
        assert False, "Should have raised ConnectionError"
    except requests.ConnectionError:
        pass

    assert mock_request.call_count == 3


@patch("github_client.time.sleep")
@patch("github_client.requests.request")
def test_retry_returns_last_500_response(mock_request, mock_sleep, monkeypatch):
    """If all retries return 500, last response is returned."""
    monkeypatch.setattr(checks_config, "github_token", "tok123")

    resp_500 = _mock_response(500, ok=False)
    mock_request.return_value = resp_500

    result = github_client._request_with_retry("GET", "https://api.github.com/test", max_retries=3)

    assert result.status_code == 500
    assert mock_request.call_count == 3
