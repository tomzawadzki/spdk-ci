#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from datetime import datetime, timedelta, timezone
from pygerrit2 import GerritRestAPI
import os
import sys
from dataclasses import dataclass, field
import json
import requests
import logging
import re
import threading
import time
import queue
import jinja2
from collections import deque

@dataclass
class ForwarderConfig:
    test_mode: bool = False
    log_level: str = "INFO"
    github_token: str = ""
    github_repo: str = "spdk/spdk-ci"
    queue_process_interval: int = 60
    max_running_workflows: int = 3
    output_dir: str = "/output"
    gerrit_url: str = "https://review.spdk.io"
    recovery_window_days: int = 7
    gerrit_query_limit: int = 300
    github_dispatch_url: str = field(init=False)
    github_workflow_runs_urls: list = field(init=False)

    def __post_init__(self):
        self.test_mode = os.getenv("FORWARDER_TEST_MODE", str(self.test_mode)).lower() == "true"
        self.log_level = os.getenv("LOG_LEVEL", self.log_level).upper()
        self.github_token = os.getenv("FORWARDER_GITHUB_TOKEN", self.github_token)

        if not self.github_token and not self.test_mode:
            print("CRITICAL: FORWARDER_GITHUB_TOKEN environment variable is required when not in test mode.", file=sys.stderr)
            sys.exit(1)

        self.github_repo = os.getenv("FORWARDER_GITHUB_REPO", self.github_repo)
        github_repo_url = f"https://api.github.com/repos/{self.github_repo}"
        self.github_dispatch_url = f"{github_repo_url}/dispatches"
        self.github_workflow_runs_urls = [
            f"{github_repo_url}/actions/workflows/gerrit-webhook-handler.yml/runs",
            f"{github_repo_url}/actions/workflows/spdk-site-build.yml/runs",
        ]

        self.output_dir = os.getenv("OUTPUT_DIR", self.output_dir)
        self.gerrit_url = os.getenv("GERRIT_URL", self.gerrit_url).rstrip("/")
        for attr in ['queue_process_interval', 'max_running_workflows', 'recovery_window_days', 'gerrit_query_limit']:
            try:
                setattr(self, attr, int(os.getenv(f"FORWARDER_{attr.upper()}", str(getattr(self, attr)))))
            except Exception:
                print(f"CRITICAL: FORWARDER_{attr.upper()} must be an integer.", file=sys.stderr)
                sys.exit(1)

config = ForwarderConfig()
# Matches run-name pattern "(12345/5)Subject" from gerrit-webhook-handler.yml
DISPLAY_TITLE_RE = re.compile(r"^\((\d+)/(\d+)\)(.*)")

event_queue: queue.Queue[dict[str, Any]] = queue.Queue()


def _github_headers():
    return {
        "Authorization": f"Bearer {config.github_token}",
        "Accept": "application/vnd.github+json",
    }


def _get_workflow_runs():
    """Fetch all active workflow runs (in_progress, waiting, queued) from GitHub."""
    runs = []
    for url in config.github_workflow_runs_urls:
        for status in ("in_progress", "waiting", "queued"):
            try:
                response = requests.get(
                    url, headers=_github_headers(),
                    params={"status": status, "event": "repository_dispatch", "per_page": 100})
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

    if config.test_mode:
        logging.info("Test mode; not forwarding to GitHub Actions.")
        return True

    try:
        response = requests.post(config.github_dispatch_url, headers=_github_headers(), json=body)
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


def _build_in_progress_rows():
    """Build status page rows for workflows currently running on GitHub."""
    rows = []
    for run in _get_workflow_runs():
        m = DISPLAY_TITLE_RE.search(run.get("display_title", ""))
        if not m:
            continue
        change_number = int(m.group(1))
        gerrit_repo = "spdk"
        # Coupled to the workflow filename; update if spdk-site-build.yml is renamed.
        if "spdk-site-build.yml" in run.get("path", ""):
            gerrit_repo = "spdk.github.io"
        rows.append({
            "change_url": f"{config.gerrit_url}/c/spdk/{gerrit_repo}/+/{change_number}",
            "change_number": change_number,
            "patchset_number": int(m.group(2)),
            "subject": m.group(3).strip(),
            "status": "In Progress",
            "run_url": run.get("html_url", ""),
        })
    return rows


def write_queue_snapshot(pending_events, dispatched_owners):
    in_progress_rows = _build_in_progress_rows()

    # Simulate the fair-scheduling order on copies so we can display the
    # estimated dispatch sequence without mutating the live state.
    remaining = dict(pending_events)
    owners_copy = deque(dispatched_owners)
    waiting_rows = []
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
        waiting_rows.append({
            "change_url": change.get("url", ""),
            "change_number": selected,
            "patchset_number": patchset.get("number", ""),
            "subject": change.get("subject", ""),
            "owner": owner or "",
            "status": "Waiting",
            "run_url": "",
        })

    env = jinja2.Environment(loader=jinja2.FileSystemLoader("./"))
    template = env.get_template("queue_status_template.html")
    html = template.render(
        in_progress_rows=in_progress_rows,
        waiting_rows=waiting_rows,
        timestamp=time.strftime("%B %d %H:%M", time.gmtime()),
        interval=config.queue_process_interval,
    )

    with open(os.path.join(config.output_dir, "queue_status.html"), "w") as f:
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
    gerrit = GerritRestAPI(url=config.gerrit_url)
    query = "".join([
        "/changes/",
        "?q=(project:spdk/spdk OR project:spdk/spdk.github.io)"
        " status:open -is:wip -is:private"
        " -label:Verified<0 -label:Verified>0"
        f" -age:{config.recovery_window_days}d",
        "&o=CURRENT_REVISION",
        "&o=DETAILED_ACCOUNTS",
        f"&n={config.gerrit_query_limit}",
    ])

    try:
        return gerrit.get(query)
    except Exception as exc:
        logging.warning(f"Error querying Gerrit: {exc}")
        return []


def list_recoverable_changes():
    """Filter Gerrit query results to changes whose current patchset was created within RECOVERY_WINDOW_DAYS."""
    cutoff_epoch = int((datetime.now(timezone.utc) - timedelta(days=config.recovery_window_days)).timestamp())
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
    project = change.get("project")
    owner = change.get("owner", {}).get("username")

    if not all([patchset_ref, subject]):
        return {}

    event_type = "spdk-site-validation" if project == "spdk/spdk.github.io" else "patchset-created"
    # Extend the fields below if GitHub workflows start using them
    return {
        "type": event_type,
        "change_number": change_number,
        "payload": {
            "type": event_type,
            "change": {
                "number": change_number,
                "subject": subject,
                "project": project,
                "url": f"{config.gerrit_url}/c/{project}/+/{change_number}",
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


def _get_change_flags(payload):
    """Return change flags (wip/private/open/status) from an event payload."""
    change = payload.get("change", {})
    return {
        "wip": change.get("wip"),
        "private": change.get("private"),
        "open": change.get("open"),
        "status": change.get("status"),
    }


def _should_drop_event(event_data):
    """Return (drop, reason) based on events flags."""
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
        time.sleep(config.queue_process_interval)

        while True:
            try:
                event_data = event_queue.get_nowait()
            except queue.Empty:
                break
            change_number = event_data["change_number"]
            if change_number in pending_events:
                logging.info(f"Replacing queued event for change {change_number}")
            pending_events[change_number] = event_data
            drop, reason = _should_drop_event(event_data)
            if drop:
                del pending_events[change_number]
                logging.info(f"Dropping event for change {change_number} ({reason})")

        if pending_events:
            to_send = config.max_running_workflows - get_active_workflow_count()
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
        project = change.get("project")
        if project == "spdk/spdk.github.io":
            event_type = "spdk-site-validation"
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
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("/var/log/forwarder.log", mode="a")
        ]
    )

    recover_queue()

    queue_thread = threading.Thread(target=process_queue, daemon=True)
    queue_thread.start()

    server_address = ('', 8000)
    httpd = HTTPServer(server_address, WebhookHandler)
    logging.info("Starting webhook forwarder on port 8000...")
    httpd.serve_forever()
