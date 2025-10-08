#!/usr/bin/env bash
set -ex

echo "SPDK: (${CHANGE_NUM}/${PATCH_SET}) ${TITLE}" >> "${GITHUB_STEP_SUMMARY}"
echo "Gerrit: <https://review.spdk.io/c/spdk/spdk/+/${CHANGE_NUM}/${PATCH_SET}>" >> "${GITHUB_STEP_SUMMARY}"

# Get latest info about a change itself
curl -s -X GET "https://review.spdk.io/changes/spdk%2Fspdk~${CHANGE_NUM}?o=DETAILED_ACCOUNTS&o=LABELS&o=SKIP_DIFFSTAT&o=CURRENT_REVISION&o=ALL_FILES" \
| tail -n +2 >  change.json

if [[ ! -s change.json ]]; then
    echo "Change ${CHANGE_NUM} not found, exiting." >> "${GITHUB_STEP_SUMMARY}"
    echo "Either it's a private change or in restricted branch." >> "${GITHUB_STEP_SUMMARY}"
    gh run cancel "${GITHUB_RUN_ID}" -R "${GITHUB_REPOSITORY}"
fi

# Do not test any change marked as WIP
# .work_in_progress is not set when false
work_in_progress="$(jq -r '.work_in_progress' change.json)"
if [[ "$work_in_progress" == "true" ]]; then
    echo "Ignore. Patch is currently WIP." >> "${GITHUB_STEP_SUMMARY}"
    gh run cancel "${GITHUB_RUN_ID}" -R "${GITHUB_REPOSITORY}"
fi

# Only test latest patch set
current_patch_set="$(jq -r '.current_revision_number' change.json)"
if ((current_patch_set != PATCH_SET)); then
    echo "Ignore. Patch set $PATCH_SET is not the latest." >> "${GITHUB_STEP_SUMMARY}"
    gh run cancel "${GITHUB_RUN_ID}" -R "${GITHUB_REPOSITORY}"
fi

# Test only changes without a Verified vote already present
verified=$(jq -r ".labels.Verified.all[]? | select(.username==\"${GERRIT_BOT_USER}\").value" change.json)
if ((verified != 0)); then
    echo "Ignore. Patch already has a vote from CI." >> "${GITHUB_STEP_SUMMARY}"
    gh run cancel "${GITHUB_RUN_ID}" -R "${GITHUB_REPOSITORY}"
fi

# Get list of files to skip some tests later depending on what files were touched
echo "changed_files=$(jq -c -r '.revisions[].files | keys' change.json)" >> "$GITHUB_OUTPUT"
