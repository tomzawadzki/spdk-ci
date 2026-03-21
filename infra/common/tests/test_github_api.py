"""Tests for common.github_api — shared HTTP helpers and GitHub API functions."""

from unittest.mock import patch, MagicMock

import requests

from common import github_api


def _mock_response(status_code=200, json_data=None, ok=True, text=""):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = ok
    resp.text = text
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# github_headers
# ---------------------------------------------------------------------------

def test_github_headers():
    h = github_api.github_headers("tok123")
    assert h["Authorization"] == "Bearer tok123"
    assert "github" in h["Accept"]
    assert "X-GitHub-Api-Version" in h


# ---------------------------------------------------------------------------
# request_with_retry
# ---------------------------------------------------------------------------

@patch("common.github_api.time.sleep")
@patch("common.github_api.requests.request")
def test_retry_on_server_error(mock_request, mock_sleep):
    """Retries on 500 status, then succeeds on 200."""
    error_resp = _mock_response(500, ok=False, text="Internal Server Error")
    ok_resp = _mock_response(200)
    mock_request.side_effect = [error_resp, ok_resp]

    result = github_api.request_with_retry("GET", "https://api.github.com/test")

    assert result.status_code == 200
    assert mock_request.call_count == 2
    mock_sleep.assert_called_once_with(1)  # 2**0 = 1


@patch("common.github_api.time.sleep")
@patch("common.github_api.requests.request")
def test_retry_on_connection_error(mock_request, mock_sleep):
    """Retries on ConnectionError, then succeeds."""
    mock_request.side_effect = [
        requests.ConnectionError("Connection refused"),
        _mock_response(200),
    ]

    result = github_api.request_with_retry("GET", "https://api.github.com/test")

    assert result.status_code == 200
    assert mock_request.call_count == 2


@patch("common.github_api.time.sleep")
@patch("common.github_api.requests.request")
def test_retry_exhausted_raises(mock_request, mock_sleep):
    """ConnectionError on all retries raises."""
    mock_request.side_effect = requests.ConnectionError("fail")

    try:
        github_api.request_with_retry(
            "GET", "https://api.github.com/test", max_retries=3)
        assert False, "Should have raised ConnectionError"
    except requests.ConnectionError:
        pass

    assert mock_request.call_count == 3


@patch("common.github_api.time.sleep")
@patch("common.github_api.requests.request")
def test_retry_returns_last_500_response(mock_request, mock_sleep):
    """If all retries return 500, last response is returned."""
    resp_500 = _mock_response(500, ok=False)
    mock_request.return_value = resp_500

    result = github_api.request_with_retry(
        "GET", "https://api.github.com/test", max_retries=3)

    assert result.status_code == 500
    assert mock_request.call_count == 3


# ---------------------------------------------------------------------------
# trigger_repository_dispatch
# ---------------------------------------------------------------------------

@patch("common.github_api.request_with_retry")
def test_trigger_repository_dispatch(mock_retry):
    mock_retry.return_value = _mock_response(204)

    github_api.trigger_repository_dispatch(
        "tok", "myorg/myrepo", "per-patch-event", {"change": {"number": 1}})

    mock_retry.assert_called_once()
    args, kwargs = mock_retry.call_args
    assert args[0] == "POST"
    assert args[1] == "https://api.github.com/repos/myorg/myrepo/dispatches"
    assert kwargs["json"]["event_type"] == "per-patch-event"
    assert kwargs["json"]["client_payload"] == {"change": {"number": 1}}


# ---------------------------------------------------------------------------
# get_workflow_runs
# ---------------------------------------------------------------------------

@patch("common.github_api.request_with_retry")
def test_get_workflow_runs_with_workflow_file(mock_retry):
    mock_retry.return_value = _mock_response(
        200, json_data={"workflow_runs": [{"id": 1}]})

    runs = github_api.get_workflow_runs(
        "tok", "org/repo", workflow_file="ci.yml",
        statuses=["in_progress"])

    assert len(runs) == 1
    args, kwargs = mock_retry.call_args
    assert "workflows/ci.yml/runs" in args[1]


@patch("common.github_api.request_with_retry")
def test_get_workflow_runs_without_workflow_file(mock_retry):
    mock_retry.return_value = _mock_response(
        200, json_data={"workflow_runs": [{"id": 2}]})

    runs = github_api.get_workflow_runs("tok", "org/repo",
                                        statuses=["queued"])

    assert len(runs) == 1
    args, _ = mock_retry.call_args
    assert args[1].endswith("/actions/runs")


# ---------------------------------------------------------------------------
# rerun_failed_jobs
# ---------------------------------------------------------------------------

@patch("common.github_api.request_with_retry")
def test_rerun_failed_jobs(mock_retry):
    mock_retry.return_value = _mock_response(201)

    github_api.rerun_failed_jobs("tok", "org/repo", 42)

    args, _ = mock_retry.call_args
    assert args[0] == "POST"
    assert "actions/runs/42/rerun-failed-jobs" in args[1]


# ---------------------------------------------------------------------------
# get_workflow_run
# ---------------------------------------------------------------------------

@patch("common.github_api.request_with_retry")
def test_get_workflow_run(mock_retry):
    mock_retry.return_value = _mock_response(
        200, json_data={"id": 55, "status": "completed"})

    result = github_api.get_workflow_run("tok", "org/repo", 55)

    assert result == {"id": 55, "status": "completed"}
    args, _ = mock_retry.call_args
    assert "actions/runs/55" in args[1]


# ---------------------------------------------------------------------------
# check_response
# ---------------------------------------------------------------------------

def test_check_response_ok():
    """No exception on a successful response."""
    resp = _mock_response(200)
    github_api.check_response(resp, "test_action")


def test_check_response_error():
    """Raises on a failed response."""
    resp = _mock_response(404, ok=False, text="Not Found")
    resp.raise_for_status.side_effect = requests.HTTPError("404")

    try:
        github_api.check_response(resp, "test_action")
        assert False, "Should have raised"
    except requests.HTTPError:
        pass
