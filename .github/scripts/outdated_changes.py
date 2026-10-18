#!/usr/bin/env python3

import os
import logging
from pygerrit2 import GerritRestAPI, HTTPBasicAuth
from requests import HTTPError

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
GERRIT_USERNAME = os.getenv("GERRIT_USERNAME")
GERRIT_PASSWORD = os.getenv("GERRIT_PASSWORD")
GERRIT_BASE_URL = os.getenv("GERRIT_BASE_URL", "https://review.spdk.io")

SEVERE = " This makes it severely outdated. Please rebase your change."
STALE = " Please consider rebasing, make sure you're working with latest code base."

def get_changes(gerrit, behind):
    # behind_stacks:<N>w of the stacks plugin matches the changes whose current patch set was
    # created more than N weeks before the commit time of the tip of their branch.
    query = "".join([
        "/changes/",
        f"?q=project:spdk/spdk status:open -is:private -is:wip {behind}",
        "&o=CURRENT_REVISION&o=MESSAGES",
    ])
    logging.info(f"Querying Gerrit with: {query}")
    return gerrit.get(query)

def get_current_patch_comments(messages, current_revision_number):
    comments = []
    for message in messages:
        if message.get("_revision_number") == current_revision_number:
            comments.append(message["message"])
    return comments

def process_changes(gerrit, changes, weeks, message_substr, vote):
    for change in changes:
        change_id = change.get("_number")
        project = change.get("project")
        subject = change.get("subject", "N/A")
        owner = change.get("owner", {}).get("name", "Unknown")
        url = os.path.join(GERRIT_BASE_URL, "c", project, '+', str(change_id))

        existing_comments = get_current_patch_comments(change["messages"], change.get("current_revision_number"))
        if any(message_substr in comment for comment in existing_comments):
            # We already sent this comment, skip.
            continue

        logging.info(f"Processing change {url} - {subject} by {owner}")
        message = f"OUTDATED PATCH WARNING: Your change has not been updated for at least {weeks} weeks."
        send_comment(gerrit, change_id, message + message_substr, vote)

def send_comment(gerrit, change_id, message, vote):
    json_data = {"message": message}
    if vote is not None:
        json_data["labels"] = {"Verified": vote}

    logging.info(f"Sending comment to change {change_id}: {message} (Verified={vote})")
    try:
        gerrit.post(f"/changes/{change_id}/revisions/current/review", json=json_data)
        logging.info(f"Comment sent successfully to change {change_id}.")
    except Exception as e:
        logging.error(f"Failed to send comment to change {change_id}: {e}")

def main():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    auth = HTTPBasicAuth(GERRIT_USERNAME, GERRIT_PASSWORD)
    gerrit = GerritRestAPI(url=GERRIT_BASE_URL, auth=auth)

    try:
        # Changes more than twelve weeks behind are skipped, we don't want VERY old changes to
        # flood Gerrit dashboard.
        severe = get_changes(gerrit, "behind_stacks:4w -behind_stacks:12w")
        stale = get_changes(gerrit, "behind_stacks:2w -behind_stacks:4w")
        process_changes(gerrit, severe, 4, SEVERE, -1)
        process_changes(gerrit, stale, 2, STALE, None)
    except HTTPError as e:
        # Without the stacks plugin Gerrit does not know behind_stacks:, so there is nothing to do.
        if e.response is not None and e.response.status_code == 400 and "Unsupported operator" in e.response.text:
            print(f"::warning::Skipping the scan: {e.response.text.strip()}")
            return
        logging.error(f"An error occurred: {e}")
        exit(1)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        exit(1)

if __name__ == "__main__":
    main()
