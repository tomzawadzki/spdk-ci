#!/usr/bin/env python3
"""Keep manual job choices aligned with the autorun catalog."""

import json
import sys
from pathlib import Path

import yaml


def main() -> int:
    catalog = json.loads(Path(".github/common-jobs.json").read_text())
    names = [job["name"] for job in catalog]
    if len(set(names)) != len(names) or "all" in names:
        print("Common job names must be unique and cannot use the reserved name 'all'", file=sys.stderr)
        return 1

    workflow = yaml.load(
        Path(".github/workflows/spdk-common-tests.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    choices = workflow["on"]["workflow_dispatch"]["inputs"]["job"]["options"]
    if choices != ["all", *names]:
        print("Update the job dropdown to match .github/common-jobs.json", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
