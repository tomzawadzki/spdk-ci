"""Process GitHub webhook events."""

import hashlib
import hmac
import logging
import re

from config import config
from common.gerrit_helpers import post_review
import database

logger = logging.getLogger(__name__)


def verify_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """Validate GitHub webhook HMAC-SHA256 signature."""
    if not config.github_webhook_secret:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature check")
        return True
    if not signature_header:
        return False

    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False

    expected = hmac.new(
        config.github_webhook_secret.encode(),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix(prefix))


def handle_workflow_run(payload: dict):
    """Handle workflow_run webhook events (requested, in_progress, completed)."""
    action = payload.get("action")
    run = payload.get("workflow_run", {})
    github_run_id = run.get("id")
    if not github_run_id:
        logger.warning("workflow_run event missing run id")
        return

    logger.info("workflow_run.%s: run_id=%d name=%s",
                action, github_run_id, run.get("name"))

    existing = database.get_run_by_github_id(github_run_id)
    if existing:
        change = existing["gerrit_change_number"]
        patchset = existing["gerrit_patchset_number"]
        project = existing["gerrit_project"]
    else:
        change, patchset, project = _extract_gerrit_info(run)
        if not change or not patchset:
            logger.warning("Could not extract Gerrit info for run %d", github_run_id)
            return

    database.upsert_workflow_run(
        gerrit_change_number=change,
        gerrit_patchset_number=patchset,
        gerrit_project=project,
        github_run_id=github_run_id,
        github_run_number=run.get("run_number"),
        github_run_attempt=run.get("run_attempt", 1),
        workflow_name=run.get("name"),
        status=run.get("status", "queued"),
        conclusion=run.get("conclusion"),
        html_url=run.get("html_url"),
        event_type=run.get("event"),
    )

    if action == "completed":
        _post_verified_vote(change, patchset, run.get("conclusion"))


def _post_verified_vote(change: int, patchset: int, conclusion: str | None):
    """Post a Verified label to Gerrit based on the workflow conclusion."""
    if not config.gerrit_user or not config.gerrit_password:
        logger.info("Gerrit credentials not configured — skipping Verified vote")
        return

    if conclusion == "success":
        value = 1
        message = "Build Successful: all CI jobs passed."
    elif conclusion == "failure":
        value = -1
        message = "Build Failed: one or more CI jobs failed."
    else:
        logger.info("Workflow conclusion is '%s' — not posting a Verified vote",
                     conclusion)
        return

    try:
        post_review(
            gerrit_url=config.gerrit_url,
            change_number=change,
            patchset_number=patchset,
            label="Verified",
            value=value,
            message=message,
            username=config.gerrit_user,
            password=config.gerrit_password,
        )
    except Exception as exc:
        logger.error("Error posting Verified vote for change %d/%d: %s",
                     change, patchset, exc)


def handle_workflow_job(payload: dict):
    """Handle workflow_job webhook events (queued, in_progress, completed)."""
    action = payload.get("action")
    job = payload.get("workflow_job", {})
    github_job_id = job.get("id")
    github_run_id = job.get("run_id")
    if not github_job_id or not github_run_id:
        logger.warning("workflow_job event missing job or run id")
        return

    logger.info("workflow_job.%s: job_id=%d run_id=%d name=%s",
                action, github_job_id, github_run_id, job.get("name"))

    run = database.get_run_by_github_id(github_run_id)
    if not run:
        logger.warning("No tracked workflow run for run_id=%d, ignoring job %d",
                        github_run_id, github_job_id)
        return

    database.upsert_workflow_job(
        workflow_run_id=run["id"],
        github_job_id=github_job_id,
        name=job.get("name", "unknown"),
        status=job.get("status", "queued"),
        conclusion=job.get("conclusion"),
        html_url=job.get("html_url"),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        runner_name=job.get("runner_name"),
    )


def _extract_gerrit_info(run: dict) -> tuple[int | None, int | None, str]:
    """Try to extract Gerrit change/patchset from a workflow run.

    Strategies:
    1. Parse from the run display_title or head_commit message
       e.g. "change/12345/5" or "Change 12345 Patchset 5"
    2. Parse from the head_branch if it encodes change info
    """
    ref_pattern = re.compile(r"(?:refs/)?changes?/\d{1,2}/(\d+)/(\d+)")
    text_pattern = re.compile(r"[Cc]hange[:\s]+(\d+).*?[Pp]atch[Ss]et[:\s]+(\d+)")

    candidates = [
        run.get("display_title", ""),
        run.get("head_branch", ""),
    ]
    head_commit = run.get("head_commit") or {}
    candidates.append(head_commit.get("message", ""))

    for text in candidates:
        m = ref_pattern.search(text)
        if m:
            return int(m.group(1)), int(m.group(2)), ""
        m = text_pattern.search(text)
        if m:
            return int(m.group(1)), int(m.group(2)), ""

    return None, None, ""
