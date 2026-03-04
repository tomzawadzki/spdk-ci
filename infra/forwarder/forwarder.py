#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
import os
import json
import requests
import logging
import re
import threading
import time
import queue
import jinja2

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO_URL = os.getenv("GITHUB_REPO_URL", "https://api.github.com/repos/spdk/spdk-ci")
GITHUB_DISPATCH_URL = f"{GITHUB_REPO_URL}/dispatches"
GITHUB_WORKFLOW_RUNS_URL = f"{GITHUB_REPO_URL}/actions/workflows/gerrit-webhook-handler.yml/runs"
QUEUE_PROCESS_INTERVAL = int(os.getenv("QUEUE_PROCESS_INTERVAL", "60"))
MAX_RUNNING_WORKFLOWS = int(os.getenv("MAX_RUNNING_WORKFLOWS", "3"))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")

event_queue: queue.Queue[dict[str, Any]] = queue.Queue()

# This matches the pattern used in parse_false_positive_comment.sh
FALSE_POSITIVE_PATTERN = re.compile(
    r"patch set \d+:\n\nfalse positive:\s*#?\d+$",
    re.IGNORECASE
)

def post_event_to_github(event_type, payload):
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    body = {
        "event_type": event_type,
        "client_payload": payload
    }

    if not TEST_MODE:
        response = requests.post(GITHUB_DISPATCH_URL, headers=headers, json=body)
        logging.info(f"GitHub Action Trigger Response: {response.status_code} {response.text}")
    else:
        logging.info("Test mode; not forwarding to GitHub Actions.")

def get_active_workflow_count():
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    count = 0
    for status in ("in_progress", "waiting", "queued"):
        try:
            response = requests.get(
                GITHUB_WORKFLOW_RUNS_URL, headers=headers,
                params={"status": status})
            if response.status_code == 200:
                count += response.json().get("total_count", 0)
            else:
                logging.warning(f"Failed to query workflow runs (status={status}): {response.status_code}")
        except requests.RequestException as e:
            logging.warning(f"Error querying workflow runs (status={status}): {e}")
    return count

def write_queue_snapshot(pending_events):
    rows = []
    for change_number, event_data in pending_events.items():
        payload = event_data.get("payload", {})
        change = payload.get("change", {})
        patchset = payload.get("patchSet", {})
        rows.append({
            "change_url": change.get("url", ""),
            "change_number": change_number,
            "patchset_number": patchset.get("number", ""),
            "subject": change.get("subject", ""),
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

def process_queue():
    pending_events: dict[int, dict[str, Any]] = {}

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
                for change_number in list(pending_events):
                    if to_send <= 0:
                        break
                    event_data = pending_events.pop(change_number)
                    post_event_to_github(event_data["type"], event_data["payload"])
                    to_send -= 1

        write_queue_snapshot(pending_events)

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
            comment = payload.get("comment", "")
            if not comment or not FALSE_POSITIVE_PATTERN.search(comment):
                logging.info("Ignoring comment-added event: comment does not match false positive pattern")
            else:
                post_event_to_github(event_type, payload)

            self.send_webhook_response()
            return

        change_number = payload.get("change", {}).get("number")
        event_data = {
            "type": event_type,
            "payload": payload,
            "change_number": change_number
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

    queue_thread = threading.Thread(target=process_queue, daemon=True)
    queue_thread.start()

    server_address = ('', 8000)
    httpd = HTTPServer(server_address, WebhookHandler)
    logging.info("Starting webhook forwarder on port 8000...")
    httpd.serve_forever()
