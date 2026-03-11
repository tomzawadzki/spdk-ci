#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from datetime import datetime, timedelta, timezone
from pygerrit2 import GerritRestAPI
import os
import json
import requests
import logging
import re
import threading
import time
import queue
import jinja2
from collections import deque

TEST_MODE = (os.getenv("TEST_MODE") or "false").lower() == "true"
LOG_LEVEL = (os.getenv("LOG_LEVEL") or "INFO").upper()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or None
GITHUB_REPO_URL = os.getenv("GITHUB_REPO_URL") or "https://api.github.com/repos/spdk/spdk-ci"
GITHUB_DISPATCH_URL = f"{GITHUB_REPO_URL}/dispatches"
GITHUB_WORKFLOW_RUNS_URL = f"{GITHUB_REPO_URL}/actions/workflows/gerrit-webhook-handler.yml/runs"
QUEUE_PROCESS_INTERVAL = int(os.getenv("QUEUE_PROCESS_INTERVAL") or "60")
MAX_RUNNING_WORKFLOWS = int(os.getenv("MAX_RUNNING_WORKFLOWS") or "3")
OUTPUT_DIR = os.getenv("OUTPUT_DIR") or "/output"
GERRIT_URL = os.getenv("GERRIT_URL") or "https://review.spdk.io"
RECOVERY_WINDOW_DAYS = int(os.getenv("RECOVERY_WINDOW_DAYS") or "7")
GERRIT_QUERY_LIMIT = int(os.getenv("GERRIT_QUERY_LIMIT") or "300")
# Matches run-name pattern "(12345/5)Subject" from gerrit-webhook-handler.yml
DISPLAY_TITLE_RE = re.compile(r"^\((\d+)/(\d+)\)(.*)")

event_queue: queue.Queue[dict[str, Any]] = queue.Queue()


def _github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def _get_workflow_runs():
    """Fetch all active workflow runs (in_progress, waiting, queued) from GitHub."""
    runs = []
    for status in ("in_progress", "waiting", "queued"):
        try:
            response = requests.get(
                GITHUB_WORKFLOW_RUNS_URL, headers=_github_headers(),
                params={"status": status, "per_page": 100})
            if response.status_code == 200:
                runs.extend(response.json().get("workflow_runs", []))
            else:
                logging.warning(f"Failed to query workflow runs (status={status}): {response.status_code}")
        except requests.RequestException as exc:
            logging.warning(f"Error querying workflow runs (status={status}): {exc}")
    return runs


def post_event_to_github(event_type, payload):
    body = {
        "event_type": event_type,
        "client_payload": payload
    }

    if TEST_MODE:
        logging.info("Test mode; not forwarding to GitHub Actions.")
        return True

    try:
        response = requests.post(GITHUB_DISPATCH_URL, headers=_github_headers(), json=body)
    except requests.RequestException as exc:
        logging.warning(f"GitHub action trigger failed with request error: {exc}")
        return False

    if 200 <= response.status_code < 300:
        logging.info(f"GitHub Action Trigger Response: {response.status_code} {response.text}")
        return True

    logging.warning(f"GitHub Action Trigger failed: {response.status_code} {response.text}")
    return False


def get_active_workflow_count():
    return len(_get_workflow_runs())

def write_queue_snapshot(pending_events, dispatched_owners):
    # Simulate the fair-scheduling order on copies so we can display the
    # estimated dispatch sequence without mutating the live state.
    remaining = dict(pending_events)
    owners_copy = deque(dispatched_owners)
    rows = []
    while remaining:
        selected = _select_fair_event(remaining, owners_copy)
        event_data = remaining.pop(selected)
        owner = _get_event_owner(event_data)
        if owner in owners_copy:
            owners_copy.remove(owner)
        if owner is not None:
            owners_copy.append(owner)

        payload = event_data.get("payload", {})
        change = payload.get("change", {})
        patchset = payload.get("patchSet", {})
        rows.append({
            "change_url": change.get("url", ""),
            "change_number": selected,
            "patchset_number": patchset.get("number", ""),
            "subject": change.get("subject", ""),
            "owner": owner or "",
        })

    env = jinja2.Environment(loader=jinja2.FileSystemLoader("./"))
    template = env.get_template("queue_status_template.html")
    html = template.render(
        rows=rows,
        timestamp=time.strftime("%B %d %H:%M", time.gmtime()),
        interval=QUEUE_PROCESS_INTERVAL,
    )

    with open(os.path.join(OUTPUT_DIR, "queue_status.html"), "w") as f:
        f.write(html)


def _parse_gerrit_timestamp_to_unix(timestamp):
    """Parse Gerrit REST timestamp (e.g. "2025-01-15 10:30:00.000000000") to Unix epoch int."""
    if not timestamp:
        return None
    try:
        return int(datetime.fromisoformat(timestamp).timestamp())
    except Exception:
        logging.warning(f"Failed to parse Gerrit timestamp: {timestamp}")
        return None


def _get_current_revision(change):
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


def query_gerrit_for_recovery():
    """Query Gerrit REST API for open changes without a Verified label."""
    gerrit = GerritRestAPI(url=GERRIT_URL)
    query = "".join([
        "/changes/",
        "?q=project:spdk/spdk status:open -is:wip -is:private"
        " -label:Verified<0 -label:Verified>0"
        f" -age:{RECOVERY_WINDOW_DAYS}d",
        "&o=CURRENT_REVISION",
        "&o=DETAILED_ACCOUNTS",
        f"&n={GERRIT_QUERY_LIMIT}",
    ])

    try:
        return gerrit.get(query)
    except Exception as exc:
        logging.warning(f"Error querying Gerrit: {exc}")
        return []


def list_recoverable_changes():
    """Filter Gerrit query results to changes whose current patchset was created within RECOVERY_WINDOW_DAYS."""
    cutoff_epoch = int((datetime.now(timezone.utc) - timedelta(days=RECOVERY_WINDOW_DAYS)).timestamp())
    recovered = []

    for change in query_gerrit_for_recovery():
        current_revision_data = _get_current_revision(change)
        if not current_revision_data:
            continue
        patchset_created = _parse_gerrit_timestamp_to_unix(current_revision_data.get("created"))
        if patchset_created is None:
            continue
        if patchset_created >= cutoff_epoch:
            recovered.append(change)

    return recovered


def build_recovery_event(change):
    """Build a minimal fake patchset-created event from a Gerrit REST API change object."""
    current_revision_data = _get_current_revision(change)
    if not current_revision_data:
        return {}

    patchset_created = _parse_gerrit_timestamp_to_unix(current_revision_data.get("created"))
    if patchset_created is None:
        return {}

    try:
        patchset_number = int(current_revision_data.get("_number"))
        change_number = int(change.get("_number"))
    except Exception:
        return {}
    patchset_ref = current_revision_data.get("ref")
    subject = change.get("subject")
    owner = change.get("owner", {}).get("username")

    if not all([patchset_ref, subject]):
        return {}

    # Extend the fields below if GitHub workflows start using them
    return {
        "type": "patchset-created",
        "change_number": change_number,
        "payload": {
            "type": "patchset-created",
            "change": {
                "number": change_number,
                "subject": subject,
                "url": f"{GERRIT_URL}/c/spdk/spdk/+/{change_number}",
                "owner": {"username": owner},
            },
            "patchSet": {
                "number": patchset_number,
                "ref": patchset_ref,
                "createdOn": patchset_created,
            },
        },
    }


def get_active_workflow_changes():
    """Return a set of (change_number, patchset_number) for active workflow runs."""
    active = set()
    for run in _get_workflow_runs():
        m = DISPLAY_TITLE_RE.search(run.get("display_title", ""))
        if m:
            change_number = int(m.group(1))
            patchset_number = int(m.group(2))
            active.add((change_number, patchset_number))
    return active


def recover_queue():
    """Enqueue recovery events from Gerrit for changes missing a Verified label."""
    try:
        changes = list_recoverable_changes()
        events = [e for c in changes if (e := build_recovery_event(c))]
        events.sort(key=lambda e: e["payload"]["patchSet"]["createdOn"])

        active = get_active_workflow_changes()
        events = [e for e in events
                  if (e["change_number"], e["payload"]["patchSet"]["number"]) not in active]

        for event in events:
            event_queue.put(event)
        logging.info(f"Recovery: enqueued {len(events)} events from Gerrit")
    except Exception as exc:
        logging.warning(f"Recovery failed (continuing startup): {exc}")


def _get_event_owner(event_data):
    """Return the change owner username, or None if missing."""
    return event_data.get("payload", {}).get("change", {}).get("owner", {}).get("username")


def _select_fair_event(pending_events, dispatched_owners):
    """Pick the next change_number to dispatch using owner-based round-robin.

    New owners (not in dispatched_owners) get priority, in insertion order.
    When all pending owners have been dispatched before, the one dispatched
    longest ago (front of deque) goes next.

    Callers must ensure pending_events is non-empty; the function always
    returns a valid change_number under that precondition.
    """
    # Pass 1: prefer events from owners we haven't dispatched yet.
    # None owners (missing data) always pass this check and get priority.
    for change_number, event_data in pending_events.items():
        if _get_event_owner(event_data) not in dispatched_owners:
            return change_number

    # Pass 2: all pending owners are in the deque -- pick the one at the
    # front (dispatched longest ago) that still has a pending event.
    for candidate in dispatched_owners:
        for change_number, event_data in pending_events.items():
            if _get_event_owner(event_data) == candidate:
                return change_number


def process_queue():
    pending_events: dict[int, dict[str, Any]] = {}
    # Tracks which owners have been dispatched, ordered from least-recent
    # (front) to most-recent (back).  Used by _select_fair_event() for
    # round-robin selection.  Cleared only when the queue fully drains so
    # that owners returning mid-cycle land in the right position.
    dispatched_owners: deque[str] = deque()

    while True:
        time.sleep(QUEUE_PROCESS_INTERVAL)

        while True:
            try:
                event_data = event_queue.get_nowait()
            except queue.Empty:
                break
            change_number = event_data["change_number"]
            if change_number in pending_events:
                logging.info(f"Replacing queued event for change {change_number}")
            pending_events[change_number] = event_data

        if pending_events:
            to_send = MAX_RUNNING_WORKFLOWS - get_active_workflow_count()
            if to_send <= 0:
                logging.info(f"Max workflows reached, deferring {len(pending_events)} events")
            else:
                while to_send > 0 and pending_events:
                    selected = _select_fair_event(pending_events, dispatched_owners)
                    event_data = pending_events[selected]
                    if not post_event_to_github(event_data["type"], event_data["payload"]):
                        break
                    del pending_events[selected]
                    owner = _get_event_owner(event_data)
                    if owner in dispatched_owners:
                        dispatched_owners.remove(owner)
                    if owner is not None:
                        dispatched_owners.append(owner)
                    to_send -= 1
        else:
            # Queue fully drained -- reset round-robin so everyone starts
            # fresh when new events arrive.
            dispatched_owners.clear()

        write_queue_snapshot(pending_events, dispatched_owners)

class WebhookHandler(BaseHTTPRequestHandler):
    def send_webhook_response(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Webhook received')

    def do_POST(self):
        logging.info(f"Received POST request on {self.path}")

        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        payload = json.loads(post_data.decode('utf-8'))
        logging.info(f"Request Body: {post_data.decode('utf-8')}")

        event_type = payload.get("type")

        # Filter comment-added events: only forward if comment matches false positive pattern
        if event_type == "comment-added":
            # This matches the pattern used in parse_false_positive_comment.sh
            false_positive_pattern = re.compile(
                r"patch set \d+:\n\nfalse positive:\s*#?\d+$",
                re.IGNORECASE
            )
            comment = payload.get("comment", "")
            if not comment or not false_positive_pattern.search(comment):
                logging.info("Ignoring comment-added event: comment does not match false positive pattern")
            else:
                post_event_to_github(event_type, payload)

            self.send_webhook_response()
            return

        change = payload.get("change", {})
        change_number = change.get("number")
        if not change.get("owner", {}).get("username"):
            logging.warning(f"Event for change {change_number} is missing owner username")
        event_data = {
            "type": event_type,
            "payload": payload,
            "change_number": change_number,
        }
        event_queue.put(event_data)

        self.send_webhook_response()

if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("/var/log/webhook_forwarder.log", mode="a")
        ]
    )

    if not GITHUB_TOKEN or not GITHUB_REPO_URL:
        logging.error("Error: GITHUB_TOKEN or GITHUB_REPO_URL environment variable is not set.")
        exit(1)

    recover_queue()

    queue_thread = threading.Thread(target=process_queue, daemon=True)
    queue_thread.start()

    server_address = ('', 8000)
    httpd = HTTPServer(server_address, WebhookHandler)
    logging.info("Starting webhook forwarder on port 8000...")
    httpd.serve_forever()
