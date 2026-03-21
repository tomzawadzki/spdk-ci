"""Shared GitHub API client used by forwarder and checks services."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
TIMEOUT = 15


def github_headers(token: str) -> dict:
    """Standard GitHub API headers."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def request_with_retry(method, url, max_retries=3, **kwargs):
    """Make an HTTP request with retry for transient server errors."""
    resp = None
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code < 500:
                return resp
            logger.warning("GitHub API %d on attempt %d: %s",
                           resp.status_code, attempt + 1, url)
        except requests.ConnectionError as e:
            logger.warning("Connection error on attempt %d: %s", attempt + 1, e)
            if attempt == max_retries - 1:
                raise
        time.sleep(2 ** attempt)
    return resp


def check_response(resp, action):
    """Log and raise on non-2xx responses."""
    if not resp.ok:
        logger.error("%s failed: %d %s", action, resp.status_code, resp.text[:500])
        resp.raise_for_status()


def trigger_repository_dispatch(token: str, repo: str, event_type: str,
                                client_payload: dict):
    """Trigger a GitHub Actions repository_dispatch event."""
    url = f"{GITHUB_API}/repos/{repo}/dispatches"
    body = {"event_type": event_type, "client_payload": client_payload}
    logger.info("Triggering repository_dispatch on %s: event_type=%s",
                repo, event_type)
    resp = request_with_retry("POST", url, json=body,
                              headers=github_headers(token), timeout=TIMEOUT)
    check_response(resp, f"trigger_dispatch({repo}, {event_type})")
    return resp


def get_workflow_runs(token: str, repo: str, workflow_file: str | None = None,
                      statuses=None):
    """Fetch workflow runs from GitHub, optionally filtered by status."""
    if workflow_file:
        url = f"{GITHUB_API}/repos/{repo}/actions/workflows/{workflow_file}/runs"
    else:
        url = f"{GITHUB_API}/repos/{repo}/actions/runs"

    runs = []
    for status in (statuses or ["in_progress", "waiting", "queued"]):
        try:
            resp = request_with_retry(
                "GET", url, headers=github_headers(token),
                params={"status": status, "per_page": 100}, timeout=TIMEOUT)
            if resp and resp.status_code == 200:
                runs.extend(resp.json().get("workflow_runs", []))
            elif resp:
                logger.warning("Failed to query workflow runs (status=%s): %d",
                               status, resp.status_code)
        except requests.RequestException as exc:
            logger.warning("Error querying workflow runs (status=%s): %s",
                           status, exc)
    return runs


def rerun_failed_jobs(token: str, repo: str, run_id: int):
    """Rerun failed jobs for a workflow run."""
    url = f"{GITHUB_API}/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs"
    logger.info("Rerunning failed jobs for run %d on %s", run_id, repo)
    resp = request_with_retry("POST", url, headers=github_headers(token),
                              timeout=TIMEOUT)
    check_response(resp, f"rerun_failed_jobs({repo}, {run_id})")
    return resp


def get_workflow_run(token: str, repo: str, run_id: int):
    """Get details of a workflow run."""
    url = f"{GITHUB_API}/repos/{repo}/actions/runs/{run_id}"
    resp = request_with_retry("GET", url, headers=github_headers(token),
                              timeout=TIMEOUT)
    check_response(resp, f"get_workflow_run({repo}, {run_id})")
    return resp.json()
