#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import json
import requests
import logging

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_ACTION_URL = os.getenv("GITHUB_ACTION_URL")

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        logging.info(f"Received POST request on {self.path}")

        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        payload = json.loads(post_data.decode('utf-8'))
        logging.info(f"Request Body: {post_data.decode('utf-8')}")


        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        body = {
            "event_type": payload["type"],
            "client_payload": payload
        }

        if not TEST_MODE:
            response = requests.post(GITHUB_ACTION_URL, headers=headers, json=body)
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

    if not GITHUB_TOKEN or not GITHUB_ACTION_URL:
        logging.error("Error: GITHUB_TOKEN or GITHUB_ACTION_URL environment variable is not set.")
        exit(1)

    server_address = ('', 8000)
    httpd = HTTPServer(server_address, WebhookHandler)
    logging.info("Starting webhook forwarder on port 8000...")
    httpd.serve_forever()
