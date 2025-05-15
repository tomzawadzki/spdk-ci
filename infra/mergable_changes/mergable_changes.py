#!/usr/bin/env python3

import os
import time
import jinja2
import logging
import datetime
from typing import Dict
from prettytable import PrettyTable
from requests import RequestException
from dataclasses import dataclass, field
from pygerrit2 import GerritRestAPI

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")
GERRIT_BASE_URL = os.getenv("GERRIT_BASE_URL", "https://review.spdk.io")
GERRIT_CHANGE_URL = os.path.join(GERRIT_BASE_URL, "c")


@dataclass
class GerritChange:
    number: int
    project: str
    subject: str
    owner: str
    revisions: dict
    blocked_by: str
    reviewed_by: str
    has_minus_one: bool = False
    has_merge_conflict: bool = False
    needs_plus_two: bool = False
    ready: bool = False
    age: datetime.timedelta = field(init=False)
    hours: int = field(init=False)
    url: str = field(init=False)

    def __post_init__(self):
        first_revision = next(iter(self.revisions))
        created = datetime.datetime.strptime(first_revision['created'], '%Y-%m-%d %H:%M:%S.%f000')
        created = created.replace(tzinfo=datetime.timezone.utc)
        self.age = datetime.datetime.now(datetime.timezone.utc) - created
        self.hours = self.age.seconds // 3600
        self.url = os.path.join(GERRIT_CHANGE_URL, self.project, '+', str(self.number))

    @classmethod
    def from_json(cls, change_json: Dict):
        ready = True
        is_mergeable = change_json['mergeable']
        is_submittable = change_json['submittable']
        has_merge_conflict = not is_mergeable
        code_reviews = change_json['labels']['Code-Review']['all']
        plus_two_crs = sum(1 for review in code_reviews if review['value'] == 2)
        minus_one_crs = sum(1 for review in code_reviews if review['value'] < 0)
        has_minus_one = minus_one_crs > 0
        needs_plus_two = not has_minus_one and not has_merge_conflict and plus_two_crs == 1
        reviewed_by = str(next((review['name'] for review in code_reviews if review['value'] == 2), None))

        if not is_submittable:
            ready = False

        return cls(
            number=change_json['_number'],
            project=change_json['project'],
            subject=change_json['subject'],
            owner=change_json['owner']['name'],
            revisions=change_json['revisions'].values(),
            blocked_by="",
            reviewed_by=reviewed_by,
            has_minus_one=has_minus_one,
            has_merge_conflict=has_merge_conflict,
            needs_plus_two=needs_plus_two,
            ready=ready
        )

    @classmethod
    def blocking_change(cls, change_json: Dict):
        return cls(
            number=change_json['_number'],
            project=change_json['project'],
            subject=change_json['subject'],
            owner=change_json['owner']['name'],
            revisions=change_json['revisions'].values(),
            blocked_by="",
            reviewed_by="",
            has_minus_one=False,
            has_merge_conflict=False,
            needs_plus_two=False,
            ready=False
        )


    def check_parents_ready(self, gerrit, all_changes):
        if self.ready:
            try:
                query = "".join([
                    "/changes/", str(self.number), "/submitted_together", "?o=DETAILED_ACCOUNTS" 
                ])
                change_series = gerrit.get(query)
            except RequestException:
                # GET operation on /submitted_together can fail with 403 if there are patches which
                # the caller cannot read, e.g. private changes. Bail out if this happens.
                # TODO: Check if using NON_VISIBLE_CHANGES option could help.
                # https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#submitted-together
                pass

            for change in reversed(change_series):
                # submitted_together contains a list data for changes submitted together including the
                # one currently inspected - skip it.
                if self.number == change['_number']:
                    continue

                parent_change = get_change_by_number(all_changes, change['_number'])
                if not parent_change:
                    # Looks like there's no +2'd parent changes so create a "blocking" one
                    parent_change = GerritChange.blocking_change(change)

                if parent_change.ready is False:
                    self.ready = False
                    self.needs_plus_two = False
                    self.blocked_by = parent_change
                    break

def get_gerrit_changes(gerrit, all_changes):
    query = "".join([
        "/changes/", "?q=project:spdk/spdk status:open label:Code-Review=2 label:Verified=1",
        "&o=CURRENT_REVISION", "&o=DETAILED_LABELS", "&o=DETAILED_ACCOUNTS", "&o=SUBMITTABLE"
    ])
    changes_json = gerrit.get(query)
    for change_json in changes_json:
        change = GerritChange.from_json(change_json)
        all_changes.append(change)

def get_change_by_number(all_changes, number):
    for change in all_changes:
        if change.number == number:
            return change

def get_ready_changes(all_changes):
    return [c for c in all_changes if c.ready]

def get_needs_plus_two_changes(all_changes):
    return [c for c in all_changes if c.needs_plus_two]

def get_minus_one_changes(all_changes):
    return [c for c in all_changes if c.has_minus_one]

def get_merge_conflict_changes(all_changes):
    return [c for c in all_changes if c.has_merge_conflict]

def get_blocked_by_changes(all_changes):
    return [c for c in all_changes if c.blocked_by]

def write_text_summary(all_changes):
    def write_and_log(line, fh):
        fh.write(line + "\n")
        logging.debug(line)

    sections = {
        "Changes ready for merge": get_ready_changes(all_changes),
        "Changes needing another +2 CR vote": get_needs_plus_two_changes(all_changes),
        "Changes with a -1 CR vote": get_minus_one_changes(all_changes),
        "Changes with a merge conflict": get_merge_conflict_changes(all_changes),
        "Changes blocked by parents in series": get_blocked_by_changes(all_changes)
    }

    timestamp = datetime.datetime.now(datetime.timezone.utc)
    with open(os.path.join(OUTPUT_DIR, "mergable_changes.txt"), "w") as fh:
        fh.write(f"Generated at {timestamp}\n")
        fh.write("Contents are re-generated every 5 minutes.\n\n\n")
        for section_name, changes in sections.items():
            write_and_log(f"{section_name}", fh)
            write_and_log("-" * len(section_name), fh)

            if changes:
                table = PrettyTable()
                table.align = "l"
                field_names = ["Number", "Subject", "Owner", "URL", "Age"]
                field_names.append("Reviewed by") if "another +2 CR" in section_name else None
                field_names.append("Blocked by") if "blocked" in section_name else None
                    
                table.field_names = field_names
                for change in changes:
                    row_values = [change.number, change.subject, change.owner, change.url, f"{change.age.days:} days {change.hours} hours"]
                    row_values.append(change.reviewed_by) if "another +2 CR" in section_name else None
                    row_values.append(change.blocked_by.url) if "blocked" in section_name else None
                    table.add_row(row_values)
                write_and_log(table.get_string() + "\n", fh)
            else:
                write_and_log("No changes in this category.\n", fh)

    template = jinja2.Environment(loader=jinja2.FileSystemLoader('./')).get_template("template.html")
    with open(os.path.join(OUTPUT_DIR, "mergable_changes.html"), "w+") as output:
        output.write(template.render(sections=sections, timestamp=timestamp.strftime("%B %d %H:%M")))


def main():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("/var/log/mergable_changes.log", mode="a")
        ]
    )

    while True:
        all_changes = []
        gerrit = GerritRestAPI(url=GERRIT_BASE_URL)
        get_gerrit_changes(gerrit, all_changes)
        for change in all_changes:
            change.check_parents_ready(gerrit, all_changes)
        all_changes.sort(key=lambda c: c.age, reverse=True)
        write_text_summary(all_changes)
        time.sleep(300)

if __name__ == '__main__':
    main()
