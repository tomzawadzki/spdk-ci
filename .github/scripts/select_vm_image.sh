#!/usr/bin/env bash
# Emit the workflow run containing the latest image for this distribution.
set -euo pipefail

distro=${1:?distribution required}
name="vm-image-${distro}_x86_64"

run_id=$(gh api --paginate \
  "/repos/${GITHUB_REPOSITORY}/actions/artifacts?name=${name}" --jq '
    .artifacts
    | sort_by(.updated_at)
    | last
    | .workflow_run.id
  ')
if [[ -z "$run_id" ]]; then
  echo "$distro is empty" >&2
  exit 1
fi
echo "run_id=$run_id"
