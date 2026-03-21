"""Minimal GitHub API client using requests."""

import logging
import time

import requests

from config import config

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
TIMEOUT = 15


def _headers():
    return {
        "Authorization": f"Bearer {config.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _check_response(resp, action):
    """Log and raise on non-2xx responses."""
    if not resp.ok:
        logger.error("%s failed: %d %s", action, resp.status_code, resp.text[:500])
        resp.raise_for_status()


def _request_with_retry(method, url, max_retries=3, **kwargs):
    """Make an HTTP request with simple retry for transient errors."""
    resp = None
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code < 500:
                return resp
            logger.warning("GitHub API %d on attempt %d: %s", resp.status_code, attempt + 1, url)
        except requests.ConnectionError as e:
            logger.warning("Connection error on attempt %d: %s", attempt + 1, e)
            if attempt == max_retries - 1:
                raise
        time.sleep(2 ** attempt)
    return resp


def trigger_workflow(event_type, client_payload):
    """Trigger a repository_dispatch workflow.

    POST /repos/{repo}/dispatches
    """
    url = f"{GITHUB_API}/repos/{config.github_repo}/dispatches"
    body = {"event_type": event_type, "client_payload": client_payload}
    logger.info("Triggering workflow on %s: event_type=%s", config.github_repo, event_type)
    resp = _request_with_retry("POST", url, json=body, headers=_headers(), timeout=TIMEOUT)
    _check_response(resp, f"trigger_workflow({config.github_repo}, {event_type})")
    return resp


def rerun_failed_jobs(run_id):
    """Rerun failed jobs for a workflow run.

    POST /repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs
    """
    url = f"{GITHUB_API}/repos/{config.github_repo}/actions/runs/{run_id}/rerun-failed-jobs"
    logger.info("Rerunning failed jobs for run %d on %s", run_id, config.github_repo)
    resp = _request_with_retry("POST", url, headers=_headers(), timeout=TIMEOUT)
    _check_response(resp, f"rerun_failed_jobs({config.github_repo}, {run_id})")
    return resp


def get_workflow_run(run_id):
    """Get details of a workflow run.

    GET /repos/{repo}/actions/runs/{run_id}
    """
    url = f"{GITHUB_API}/repos/{config.github_repo}/actions/runs/{run_id}"
    resp = _request_with_retry("GET", url, headers=_headers(), timeout=TIMEOUT)
    _check_response(resp, f"get_workflow_run({config.github_repo}, {run_id})")
    return resp.json()
