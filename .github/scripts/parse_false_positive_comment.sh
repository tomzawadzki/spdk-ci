#!/usr/bin/env bash
# TODO: Rewrite this into python, the json pulp here is unbearable

# COMMENT: ${{ fromJSON(needs.env_vars.outputs.client_payload).comment }}
# AUTHOR: ${{ fromJSON(needs.env_vars.outputs.client_payload).author.username }}
# REPO: ${{ github.repository_owner }}/spdk
# GH_REPO: ${{ github.repository }}
# GERRIT_BOT_USER: ${{ secrets.GERRIT_BOT_USER }}
# GERRIT_BOT_HTTP_PASSWD: ${{ secrets.GERRIT_BOT_HTTP_PASSWD }}
# GH_ISSUES_PAT: ${{ secrets.GH_ISSUES_PAT }}
# GERRIT_PROJECT: ${{ fromJSON(env.client_payload).change.project }}
# change_num: ${{ fromJSON(needs.env_vars.outputs.client_payload).change.number }}
# patch_set: ${{ fromJSON(needs.env_vars.outputs.client_payload).patchSet.number }}
set -euo pipefail

: "${GERRIT_PROJECT:=spdk/spdk}" "${change_num:?}" "${patch_set:?}"
[[ "$GERRIT_PROJECT" =~ ^spdk/(spdk|spdk\.github\.io)$ ]]
[[ "$change_num" =~ ^[1-9][0-9]*$ ]]
[[ "$patch_set" =~ ^[1-9][0-9]*$ ]]
GERRIT_REPO="${GERRIT_PROJECT#*/}"

spdk_repo=$REPO
gerrit_comment=$COMMENT
reported_by=$AUTHOR

gerrit_url=https://review.spdk.io/a/changes
gerrit_format_q="o=DETAILED_ACCOUNTS&o=MESSAGES&o=LABELS&o=SKIP_DIFFSTAT"

# Looking for comment thats only content is "false positive: 123", with a leeway for no spaces
# or hashtag symbol before number
if [[ ! ${gerrit_comment,,} =~ "patch set "[0-9]+:$'\n\nfalse positive:'[[:space:]]*[#]?([0-9]+)$ ]]; then
	echo "::notice title=Skipped::Comment does not include false positive phrase."
	exit 0
fi
gh_issue=${BASH_REMATCH[1]}

# Verify that the issue exists and is open
if ! gh_status=$(gh issue -R "$spdk_repo" view "$gh_issue" --json state --jq .state) \
	|| [[ "$gh_status" != "OPEN" ]]; then
	# shellcheck disable=SC2154
	curl --connect-timeout 10 --max-time 30 --retry 2 -L -X POST \
		--user "$GERRIT_BOT_USER:$GERRIT_BOT_HTTP_PASSWD" \
		--header "Content-Type: application/json" \
		--data "$(jq -n --arg message "Issue #$gh_issue does not exist or is already closed." '{message: $message}')" \
		--fail-with-body \
		"$gerrit_url/$change_num/revisions/$patch_set/review"
	echo "::error title=Invalid Issue::Comment points to incorrect GitHub issue #$gh_issue."
	exit 0
fi

# Get latest info about a change itself - first line is the XSSI mitigation string, drop it
curl --silent --show-error --connect-timeout 10 --max-time 30 --retry 2 -X GET \
	--user "$GERRIT_BOT_USER:$GERRIT_BOT_HTTP_PASSWD" \
	"$gerrit_url/spdk%2F${GERRIT_REPO}~$change_num?$gerrit_format_q" \
	| tail -n +2 | jq . | tee change.json

if [[ ! -s change.json ]]; then
	echo "::warning title=Change Not Found::Change $change_num not found. Either it's a private change or in restricted branch."
	exit 0
fi

if [[ $(jq -r '.status' change.json) != NEW || $(jq -r '.private' change.json) == true ]]; then
	echo "::notice title=Skipped::Comment posted to a closed or private change."
	exit 0
fi

# Do not test any change marked as WIP
# .work_in_progress is not set when false
work_in_progress="$(jq -r '.work_in_progress' change.json)"
if [[ "$work_in_progress" == "true" ]]; then
	echo "::notice title=Skipped::Comment posted to WIP change."
	exit 0
fi

# Only test latest patch set
current_patch_set="$(jq -r '.current_revision_number' change.json)"
if [[ "$current_patch_set" != "$patch_set" ]]; then
  echo "::notice title=Skipped::Comment posted to different ($current_patch_set) patch set."
	exit 0
fi

# False positive should be used only on changes that already have a negative Verified vote
verified=$(jq -r --arg user "$GERRIT_BOT_USER" \
	'.labels.Verified.all[]? | select(.username == $user) | .value // 0' change.json)
if [[ $verified != -1 ]]; then
	echo "::notice title=Skipped::Comment posted with no negative vote from CI."
	exit 0
fi

# Find workflow to rerun. As a sanity check grab comment meeting following criteria:
# failed build comment from most recent patch set posted by spdk-bot - this patch set
# has to be <= compared to $patch_set the workflow was triggered by.
# NOTE: Message parsing is very fragile and has to match summary job
mapfile -t fp_run_failed_messages < <(
	jq -r --arg user "$GERRIT_BOT_USER" --argjson patch "$patch_set" \
		'.messages | sort_by(._revision_number)[] |
		select(.author.username == $user and ._revision_number <= $patch) |
		select(.message | contains("Build failed. Results: ")) | .message' change.json \
		| grep "Build failed. Results: "
)

if ((${#fp_run_failed_messages[@]} == 0)); then
  echo "::error title=No Build Failure::Did not find comments indicating build failure."
  exit 1
fi

# E.g:
# Build failed. Results: [15028790454/1](https://github.com/spdk/spdk-ci/actions/runs/15028790454/attempts/3)
# Build failed. Results: [15028790454/1](https://github.com/spdk/spdk-ci/actions/runs/15028790454)
latest_failure=${fp_run_failed_messages[-1]}
if [[ ! $latest_failure =~ \]\((https://[^[:space:]\(\)]+/actions/runs/([1-9][0-9]*)(/attempts/[1-9][0-9]*)?)\) ]]; then
	echo "::error title=Invalid Build Failure::Could not parse the failed workflow URL."
	exit 1
fi
fp_run_url=${BASH_REMATCH[1]}
fp_run_id=${BASH_REMATCH[2]}

message="Another instance of this failure. Reported by @$reported_by. Log: $fp_run_url"
# Special PAT to read/write GH issues is required
GH_TOKEN=$GH_ISSUES_PAT gh issue -R "$spdk_repo" comment "$gh_issue" -b "$message"

# Rerun only failed jobs, which will rerun all dependent ones too.
gh run rerun "$fp_run_id" --failed -R "$GH_REPO"

# Reset the verified vote and leave a comment indicating that workflows were retriggered
curl --connect-timeout 10 --max-time 30 --retry 2 -L -X POST  \
	--user "$GERRIT_BOT_USER:$GERRIT_BOT_HTTP_PASSWD" \
	--header "Content-Type: application/json" \
	--data "$(jq -n '{message: "Retriggered", labels: {Verified: 0}}')" \
	--fail-with-body \
	"$gerrit_url/$change_num/revisions/$patch_set/review"
