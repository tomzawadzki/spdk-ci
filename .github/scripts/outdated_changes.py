#!/usr/bin/env python3

import os
import logging
from datetime import datetime, timezone, timedelta
from pygerrit2 import GerritRestAPI, HTTPBasicAuth

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
GERRIT_USERNAME = os.getenv("GERRIT_USERNAME")
GERRIT_PASSWORD = os.getenv("GERRIT_PASSWORD")
GERRIT_BASE_URL = os.getenv("GERRIT_BASE_URL", "https://review.spdk.io")

def parse_datetime(datetime_str):
    return datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S.%f000").replace(tzinfo=timezone.utc)

def get_open_changes(gerrit):
    query = "".join([
        "/changes/",
        "?q=project:spdk/spdk status:open -is:private -is:wip",
        "&o=CURRENT_REVISION"
    ])
    logging.info(f"Querying Gerrit with: {query}")
    return gerrit.get(query)

def get_branch_tip_date(gerrit, branch):
    try:
        query = f"/projects/spdk%2Fspdk/branches/{branch.replace('.', '%2E')}"
        logging.info(f"Querying Gerrit for branch tip: {query}")

        branch_info = gerrit.get(query)
        latest_revsion = branch_info.get("revision")

        query = f"/projects/spdk%2Fspdk/commits/{latest_revsion}"
        commit_info = gerrit.get(query)
        commit_date_str = commit_info.get("committer", {}).get("date")

        if not commit_date_str:
            logging.warning(f"No commit date found for branch {branch}.")
            return None

        return parse_datetime(commit_date_str)
    except Exception as e:
        logging.error(f"Failed to get branch tip date for {branch}: {e}")
        return None

def process_changes(gerrit, changes):
    branch_tip_dates = {}
    two_weeks = timedelta(weeks=2)
    four_weeks = timedelta(weeks=4)
    twelve_weeks = timedelta(weeks=12)

    for change in changes:
        change_id = change.get("_number")
        project = change.get("project")
        branch = change.get("branch")
        subject = change.get("subject", "N/A")
        owner = change.get("owner", {}).get("name", "Unknown")
        url = os.path.join(GERRIT_BASE_URL, "c", project, '+', str(change_id))
        revisions = change.get("revisions", {})
        current_revision = next(iter(revisions.values()), {})
        created_str = current_revision.get("created")

        if not created_str:
            logging.warning(f"Change {change_id} has no 'created' field in the current revision.")
            continue
        created = parse_datetime(created_str)

        if branch_tip_dates.get(branch):
            branch_tip_date = branch_tip_dates[branch]
        else:
            branch_tip_date = get_branch_tip_date(gerrit, branch)
            branch_tip_dates[branch] = branch_tip_date
            logging.info(f"Saved branch tip date for {branch}: {branch_tip_date}")

        time_since_branch_tip = branch_tip_date - created
        if time_since_branch_tip <= timedelta(0):
            # Change is newer than branch tip; skip it.
            continue
        if time_since_branch_tip > twelve_weeks:
            # Change is older than twelve weeks; we don't want VERY old changes to flood Gerrit dashboard,
            # so skip them.
            continue

        logging.info(f"Processing change {url} - {subject} by {owner}")
        logging.info(f"Time since last update: {time_since_branch_tip.days} days")
        message = "OUTDATED PATCH WARNING: Your change has not been updated for at least"
        message += f" {time_since_branch_tip.days // 7} weeks ({time_since_branch_tip.days} days)."
        if time_since_branch_tip > four_weeks:
            message += " This makes it severely outdated. Please rebase your change."
            send_comment(gerrit, change_id, message, -1)
        elif time_since_branch_tip > two_weeks:
            message += " Please consider rebasing, make sure you're working with latest code base."
            send_comment(gerrit, change_id, message, None)

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
        changes = get_open_changes(gerrit)
        process_changes(gerrit, changes)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        exit(1)

if __name__ == "__main__":
    main()
