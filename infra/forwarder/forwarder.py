#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import json
import requests
import logging
import re

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO_URL = os.getenv("GITHUB_REPO_URL", "https://api.github.com/repos/spdk/spdk-ci")
GITHUB_DISPATCH_URL = f"{GITHUB_REPO_URL}/dispatches"

# This matches the pattern used in parse_false_positive_comment.sh
FALSE_POSITIVE_PATTERN = re.compile(
    r"patch set \d+:\n\nfalse positive:\s*#?\d+$",
    re.IGNORECASE
)

class WebhookHandler(BaseHTTPRequestHandler):
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
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'Webhook received')
                return

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

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Webhook received')

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

    server_address = ('', 8000)
    httpd = HTTPServer(server_address, WebhookHandler)
    logging.info("Starting webhook forwarder on port 8000...")
    httpd.serve_forever()
