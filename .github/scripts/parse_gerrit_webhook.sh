#!/usr/bin/env bash
set -euo pipefail

: "${GERRIT_PROJECT:=spdk/spdk}"
[[ "$GERRIT_PROJECT" =~ ^spdk/(spdk|spdk\.github\.io)$ ]]
[[ "$CHANGE_NUM" =~ ^[0-9]+$ ]]
[[ "$PATCH_SET" =~ ^[0-9]+$ ]]
GERRIT_REPO="${GERRIT_PROJECT#*/}"

echo "spdk/${GERRIT_REPO}: (${CHANGE_NUM}/${PATCH_SET}) ${TITLE}" >> "${GITHUB_STEP_SUMMARY}"
echo "Gerrit: <https://review.spdk.io/c/spdk/${GERRIT_REPO}/+/${CHANGE_NUM}/${PATCH_SET}>" >> "${GITHUB_STEP_SUMMARY}"

# Get latest info about a change itself
curl --fail-with-body --silent --show-error \
	--connect-timeout 10 --max-time 30 --retry 2 \
	"https://review.spdk.io/changes/spdk%2F${GERRIT_REPO}~${CHANGE_NUM}?o=DETAILED_ACCOUNTS&o=LABELS&o=SKIP_DIFFSTAT&o=CURRENT_REVISION&o=ALL_FILES" \
	| tail -n +2 > change.json

if [[ ! -s change.json ]]; then
    echo "Change ${CHANGE_NUM} not found, exiting." >> "${GITHUB_STEP_SUMMARY}"
    echo "Either it's a private change or in restricted branch." >> "${GITHUB_STEP_SUMMARY}"
    gh run cancel "${GITHUB_RUN_ID}" -R "${GITHUB_REPOSITORY}"
    exit 0
fi

# Do not test any change marked as WIP
# .work_in_progress is not set when false
work_in_progress="$(jq -r '.work_in_progress' change.json)"
if [[ "$work_in_progress" == "true" ]]; then
    echo "Ignore. Patch is currently WIP." >> "${GITHUB_STEP_SUMMARY}"
    gh run cancel "${GITHUB_RUN_ID}" -R "${GITHUB_REPOSITORY}"
    exit 0
fi

# Only test latest patch set
current_patch_set="$(jq -r '.current_revision_number' change.json)"
if ((current_patch_set != PATCH_SET)); then
    echo "Ignore. Patch set $PATCH_SET is not the latest." >> "${GITHUB_STEP_SUMMARY}"
    gh run cancel "${GITHUB_RUN_ID}" -R "${GITHUB_REPOSITORY}"
    exit 0
fi

# Test only changes without a Verified vote already present
verified=$(jq -r --arg user "$GERRIT_BOT_USER" \
	'.labels.Verified.all[]? | select(.username == $user) | .value // 0' change.json)
if [[ -n $verified && $verified != 0 ]]; then
    echo "Ignore. Patch already has a vote from CI." >> "${GITHUB_STEP_SUMMARY}"
    gh run cancel "${GITHUB_RUN_ID}" -R "${GITHUB_REPOSITORY}"
    exit 0
fi

# Get list of files to skip some tests later depending on what files were touched
echo "changed_files=$(jq -c -r '.revisions[].files | keys' change.json)" >> "$GITHUB_OUTPUT"
