"""Queue manager for the SPDK Checks backend.

Ports the forwarder's fair-scheduling queue into the checks backend so that
it can eventually replace the forwarder entirely.  Accepts Gerrit webhook
events (patchset-created), deduplicates by change number, throttles against
GitHub's active workflow limit, and dispatches using owner-based round-robin.

The module is self-contained: call ``start(interval)`` from the app lifespan
and ``enqueue()`` / ``get_status()`` from endpoint handlers.
"""

import asyncio
import logging
import re
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from config import config
from common.gerrit_helpers import (
    get_gerrit_client,
    get_current_revision,
    parse_gerrit_timestamp,
    validate_change_for_ci,
)
from common.github_api import (
    trigger_repository_dispatch,
    get_workflow_runs as _get_workflow_runs_common,
)

logger = logging.getLogger(__name__)

# Matches run-name pattern "(12345/5)Subject" from gerrit-webhook-handler.yml
DISPLAY_TITLE_RE = re.compile(r"^\((\d+)/(\d+)\)(.*)")

# ---------------------------------------------------------------------------
# Internal state — protected by _lock for thread-safety
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_pending_events: dict[int, dict[str, Any]] = {}
_dispatched_owners: deque[str] = deque()
_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# Event helpers (ported from forwarder)
# ---------------------------------------------------------------------------

def _get_event_owner(event_data: dict) -> str | None:
    return (event_data.get("payload", {})
            .get("change", {})
            .get("owner", {})
            .get("username"))


def _get_change_flags(payload: dict) -> dict:
    change = payload.get("change", {})
    return {
        "wip": change.get("wip"),
        "private": change.get("private"),
        "open": change.get("open"),
        "status": change.get("status"),
    }


def _should_drop_event(event_data: dict) -> tuple[bool, str | None]:
    flags = _get_change_flags(event_data.get("payload", {}))
    if flags["wip"] is True:
        return True, "wip"
    if flags["private"] is True:
        return True, "private"
    if flags["open"] is False:
        return True, "closed"
    status = flags["status"]
    if status is not None and status != "NEW":
        return True, f"status={status}"
    return False, None


def _select_fair_event(
    pending: dict[int, dict],
    dispatched: deque[str],
) -> int:
    """Owner-based round-robin selection.

    Pass 1: prefer events whose owner has NOT been dispatched yet.
    Pass 2: pick the owner dispatched longest ago (front of deque).
    """
    for change_number, event_data in pending.items():
        if _get_event_owner(event_data) not in dispatched:
            return change_number

    for candidate in dispatched:
        for change_number, event_data in pending.items():
            if _get_event_owner(event_data) == candidate:
                return change_number

    # Fallback: first pending event (should never reach here if pending is
    # non-empty, but defensive).
    return next(iter(pending))


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _get_active_workflow_runs() -> list[dict]:
    try:
        return _get_workflow_runs_common(
            config.github_token, config.github_repo,
            workflow_file="gerrit-webhook-handler.yml")
    except Exception as exc:
        logger.warning("Failed to query active workflows: %s", exc)
        return []


def _get_active_workflow_changes() -> set[tuple[int, int]]:
    active: set[tuple[int, int]] = set()
    for run in _get_active_workflow_runs():
        m = DISPLAY_TITLE_RE.search(run.get("display_title", ""))
        if m:
            active.add((int(m.group(1)), int(m.group(2))))
    return active


def _dispatch_event(event_data: dict) -> bool:
    """Trigger a GitHub Actions workflow for the event.  Returns True on success."""
    event_type = event_data.get("type", "per-patch-event")
    payload = event_data.get("payload", {})
    try:
        trigger_repository_dispatch(
            config.github_token, config.github_repo, event_type, payload)
        return True
    except Exception as exc:
        logger.warning("GitHub dispatch failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Recovery: backfill queue from Gerrit for changes missing CI vote
# ---------------------------------------------------------------------------

def _query_gerrit_for_recovery() -> list[dict]:
    gerrit = get_gerrit_client(config.gerrit_url)
    query = "".join([
        "/changes/",
        "?q=project:spdk/spdk status:open -is:wip -is:private"
        " -label:Verified<0 -label:Verified>0"
        f" -age:{config.recovery_window_days}d",
        "&o=CURRENT_REVISION",
        "&o=DETAILED_ACCOUNTS",
        f"&n={config.gerrit_query_limit}",
    ])
    try:
        return gerrit.get(query)
    except Exception as exc:
        logger.warning("Error querying Gerrit for recovery: %s", exc)
        return []


def _build_recovery_event(change: dict) -> dict | None:
    current_rev = get_current_revision(change)
    if not current_rev:
        return None

    patchset_created = parse_gerrit_timestamp(current_rev.get("created"))
    if patchset_created is None:
        return None

    cutoff = int(
        (datetime.now(timezone.utc) - timedelta(days=config.recovery_window_days))
        .timestamp()
    )
    if patchset_created < cutoff:
        return None

    try:
        patchset_number = int(current_rev["_number"])
        change_number = int(change["_number"])
    except (KeyError, TypeError, ValueError):
        return None

    patchset_ref = current_rev.get("ref")
    subject = change.get("subject")
    owner = change.get("owner", {}).get("username")
    if not patchset_ref or not subject:
        return None

    return {
        "type": "patchset-created",
        "change_number": change_number,
        "payload": {
            "type": "patchset-created",
            "change": {
                "number": change_number,
                "subject": subject,
                "url": f"{config.gerrit_url}/c/spdk/spdk/+/{change_number}",
                "owner": {"username": owner},
            },
            "patchSet": {
                "number": patchset_number,
                "ref": patchset_ref,
                "createdOn": patchset_created,
            },
        },
    }


def recover_queue():
    """Enqueue recovery events for open changes missing a Verified vote."""
    try:
        changes = _query_gerrit_for_recovery()
        events = [e for c in changes if (e := _build_recovery_event(c))]
        events.sort(key=lambda e: e["payload"]["patchSet"]["createdOn"])

        active = _get_active_workflow_changes()
        events = [
            e for e in events
            if (e["change_number"], e["payload"]["patchSet"]["number"])
            not in active
        ]

        with _lock:
            for ev in events:
                _pending_events[ev["change_number"]] = ev
        logger.info("Recovery: enqueued %d events from Gerrit", len(events))
    except Exception as exc:
        logger.warning("Recovery failed (continuing): %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enqueue(event_data: dict):
    """Add a Gerrit event to the dispatch queue.

    The event is expected to have at minimum:
        {"type": str, "change_number": int, "payload": dict}

    If a pending event for the same change already exists, it is replaced
    (only the latest patchset matters).
    """
    change_number = event_data.get("change_number")
    if not change_number:
        logger.warning("Ignoring event with no change_number")
        return

    drop, reason = _should_drop_event(event_data)
    if drop:
        logger.info("Dropping event for change %d (%s)", change_number, reason)
        return

    with _lock:
        if change_number in _pending_events:
            logger.info("Replacing queued event for change %d", change_number)
        _pending_events[change_number] = event_data
    logger.info("Queued event for change %d", change_number)


def get_status() -> dict:
    """Return a snapshot of the current queue for API responses.

    Returns::

        {
            "pending": [
                {"change_number": 12345, "patchset_number": 5,
                 "subject": "...", "owner": "alice", "position": 1},
                ...
            ],
            "in_progress": [
                {"change_number": 12345, "patchset_number": 5,
                 "subject": "...", "html_url": "..."},
                ...
            ],
            "max_workflows": 3,
            "queue_interval_seconds": 60,
        }
    """
    with _lock:
        pending_copy = dict(_pending_events)
        owners_copy = deque(_dispatched_owners)

    # Simulate fair-scheduling order for display
    waiting: list[dict] = []
    position = 0
    remaining = dict(pending_copy)
    sim_owners = deque(owners_copy)
    while remaining:
        position += 1
        selected = _select_fair_event(remaining, sim_owners)
        ev = remaining.pop(selected)
        owner = _get_event_owner(ev)
        if owner in sim_owners:
            sim_owners.remove(owner)
        if owner is not None:
            sim_owners.append(owner)
        payload = ev.get("payload", {})
        change = payload.get("change", {})
        patchset = payload.get("patchSet", {})
        waiting.append({
            "change_number": selected,
            "patchset_number": patchset.get("number"),
            "subject": change.get("subject", ""),
            "owner": owner or "",
            "change_url": change.get("url", ""),
            "position": position,
        })

    # Active GitHub workflow runs
    in_progress: list[dict] = []
    for run in _get_active_workflow_runs():
        m = DISPLAY_TITLE_RE.search(run.get("display_title", ""))
        if m:
            in_progress.append({
                "change_number": int(m.group(1)),
                "patchset_number": int(m.group(2)),
                "subject": m.group(3).strip(),
                "html_url": run.get("html_url", ""),
            })

    return {
        "pending": waiting,
        "in_progress": in_progress,
        "max_workflows": config.max_running_workflows,
        "queue_interval_seconds": config.queue_process_interval,
    }


# ---------------------------------------------------------------------------
# Background processing loop
# ---------------------------------------------------------------------------

async def _process_loop():
    """Periodically dispatch queued events to GitHub, respecting throttle."""
    while True:
        await asyncio.sleep(config.queue_process_interval)
        try:
            _process_once()
        except Exception:
            logger.exception("Error in queue processing loop")


def _process_once():
    """Single iteration of the dispatch loop (called by background task)."""
    with _lock:
        if not _pending_events:
            _dispatched_owners.clear()
            return

        active_count = len(_get_active_workflow_runs())
        to_send = config.max_running_workflows - active_count
        if to_send <= 0:
            logger.info("Max workflows reached (%d/%d), deferring %d events",
                        active_count, config.max_running_workflows,
                        len(_pending_events))
            return

        while to_send > 0 and _pending_events:
            selected = _select_fair_event(_pending_events, _dispatched_owners)
            event_data = _pending_events[selected]
            if not _dispatch_event(event_data):
                break
            del _pending_events[selected]
            owner = _get_event_owner(event_data)
            if owner in _dispatched_owners:
                _dispatched_owners.remove(owner)
            if owner is not None:
                _dispatched_owners.append(owner)
            to_send -= 1


def start():
    """Start the background queue processor (call from FastAPI lifespan)."""
    global _task
    loop = asyncio.get_event_loop()
    _task = loop.create_task(_process_loop())
    logger.info("Queue processor started (interval=%ds, max_workflows=%d)",
                config.queue_process_interval, config.max_running_workflows)


def stop():
    """Cancel the background task."""
    global _task
    if _task:
        _task.cancel()
        _task = None
