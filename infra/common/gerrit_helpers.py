"""Shared Gerrit API helpers."""

import logging
from datetime import datetime

from pygerrit2 import GerritRestAPI
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


def get_gerrit_client(url: str) -> GerritRestAPI:
    """Create a pygerrit2 REST API client."""
    return GerritRestAPI(url=url)


def post_review(gerrit_url: str, change_number: int, patchset_number: int,
                label: str, value: int, message: str,
                username: str | None = None,
                password: str | None = None) -> bool:
    """Post a review (label + message) to a Gerrit change revision.

    Returns True on success, False on failure.  Errors are logged but
    never propagated so callers don't need to handle exceptions.
    """
    if not username or not password:
        logger.warning("Gerrit credentials not provided — skipping review post")
        return False

    try:
        auth = HTTPBasicAuth(username, password)
        gerrit = GerritRestAPI(url=gerrit_url, auth=auth)
        body = {"labels": {label: value}, "message": message}
        gerrit.post(
            f"/changes/{change_number}/revisions/{patchset_number}/review",
            json=body,
        )
        logger.info("Posted %s %+d to change %d/%d",
                     label, value, change_number, patchset_number)
        return True
    except Exception as exc:
        logger.error("Failed to post review to change %d/%d: %s",
                     change_number, patchset_number, exc)
        return False


def get_current_revision(change: dict) -> dict:
    """Extract the current revision dict from a Gerrit change object."""
    revisions = change.get("revisions")
    if not isinstance(revisions, dict):
        return {}
    current_revision = change.get("current_revision")
    if not current_revision:
        return {}
    current_revision_data = revisions.get(current_revision)
    if not isinstance(current_revision_data, dict):
        return {}
    return current_revision_data


def parse_gerrit_timestamp(timestamp: str | None) -> int | None:
    """Parse Gerrit REST timestamp to Unix epoch seconds."""
    if not timestamp:
        return None
    try:
        return int(datetime.fromisoformat(timestamp).timestamp())
    except Exception:
        logger.warning("Failed to parse Gerrit timestamp: %s", timestamp)
        return None


def validate_change_for_ci(gerrit_url: str, change_number: int,
                           patchset_number: int | None = None) -> dict:
    """Validate a change is eligible for CI.

    Checks: not WIP, not private, status NEW, optionally latest patchset.

    Returns dict with 'valid' bool and optional 'error' string.
    On success, includes 'data' with the full Gerrit change response.
    """
    gerrit = get_gerrit_client(gerrit_url)
    try:
        data = gerrit.get(
            f"/changes/{change_number}?o=CURRENT_REVISION&o=DETAILED_LABELS")
    except Exception as exc:
        logger.error("Failed to query Gerrit for change %d: %s",
                     change_number, exc)
        return {"valid": False,
                "error": f"Failed to validate change with Gerrit: {exc}"}

    if data.get("work_in_progress"):
        return {"valid": False, "error": "Change is marked WIP"}
    if data.get("is_private"):
        return {"valid": False, "error": "Change is private"}
    if data.get("status") != "NEW":
        return {"valid": False,
                "error": f"Change status is {data.get('status')}, expected NEW"}

    if patchset_number is not None:
        current_rev = data.get("revisions", {})
        latest_patchset = 0
        for rev_info in current_rev.values():
            ps = rev_info.get("_number", 0)
            if ps > latest_patchset:
                latest_patchset = ps
        if latest_patchset and patchset_number != latest_patchset:
            return {
                "valid": False,
                "error": (f"Patchset {patchset_number} is not the latest "
                          f"(latest is {latest_patchset})"),
            }

    return {"valid": True, "data": data}
