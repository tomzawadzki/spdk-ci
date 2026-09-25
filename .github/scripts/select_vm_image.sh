#!/usr/bin/env bash
# Emit artifact_id and run_id for GitHub step outputs, empty if no image exists.
set -euo pipefail

distro=${1:?distribution required}
name="vm-image-${distro}_x86_64"

# Finish pagination before selecting an image or publishing any output.
pages=$(gh api --paginate --slurp \
  "/repos/${GITHUB_REPOSITORY}/actions/artifacts?name=${name}&per_page=100")

image=$(jq -c --arg name "$name" '
  [ .[].artifacts[]
    | select(.name == $name)
    | select(.expired == false)
    | select(.workflow_run.id != null)
  ]
  | max_by([.created_at, .id])
' <<< "$pages")

artifact_id=$(jq -r '.id // empty' <<< "$image")
run_id=$(jq -r '.workflow_run.id // empty' <<< "$image")
printf 'artifact_id=%s\nrun_id=%s\n' "$artifact_id" "$run_id"
