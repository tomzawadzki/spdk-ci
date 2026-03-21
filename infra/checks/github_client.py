"""GitHub API client for checks service — thin wrapper around common.github_api."""

from config import config
from common.github_api import (
    trigger_repository_dispatch,
    rerun_failed_jobs as _rerun_failed_jobs,
    get_workflow_run as _get_workflow_run,
)


def trigger_workflow(event_type, client_payload):
    """Trigger a repository_dispatch workflow."""
    return trigger_repository_dispatch(
        config.github_token, config.github_repo, event_type, client_payload)


def rerun_failed_jobs(run_id):
    """Rerun failed jobs for a workflow run."""
    return _rerun_failed_jobs(config.github_token, config.github_repo, run_id)


def get_workflow_run(run_id):
    """Get details of a workflow run."""
    return _get_workflow_run(config.github_token, config.github_repo, run_id)
