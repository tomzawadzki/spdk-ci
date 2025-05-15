#!/usr/bin/env bash
# TODO: Rewrite this into python, the json pulp here is unbearable

# COMMENT: ${{ fromJSON(needs.env_vars.outputs.client_payload).comment }}
# AUTHOR: ${{ fromJSON(needs.env_vars.outputs.client_payload).author.username }}
# REPO: ${{ github.repository_owner }}/spdk
# GH_REPO: ${{ github.repository }}
# GERRIT_BOT_USER: ${{ secrets.GERRIT_BOT_USER }}
# GERRIT_BOT_HTTP_PASSWD: ${{ secrets.GERRIT_BOT_HTTP_PASSWD }}
# GH_ISSUES_PAT: ${{ secrets.GH_ISSUES_PAT }}
# change_num: ${{ fromJSON(needs.env_vars.outputs.client_payload).change.number }}
# patch_set: ${{ fromJSON(needs.env_vars.outputs.client_payload).patchSet.number }}
set -eu
shopt -s extglob

spdk_repo=$REPO
gerrit_comment=$COMMENT
reported_by=$AUTHOR

gerrit_url=https://review.spdk.io/a/changes
gerrit_format_q="o=DETAILED_ACCOUNTS&o=MESSAGES&o=LABELS&o=SKIP_DIFFSTAT"

# Looking for comment thats only content is "false positive: 123", with a leeway for no spaces
# or hashtag symbol before number
if [[ ! ${gerrit_comment,,} =~ "patch set "[0-9]+:$'\n\nfalse positive:'[[:space:]]*[#]?([0-9]+)$ ]]; then
	echo "Ignore. Comment does not include false positive phrase."
	exit 0
fi
gh_issue=${BASH_REMATCH[1]}

# Verify that the issue exists and is open
if ! gh_status=$(gh issue -R "$spdk_repo" view "$gh_issue" --json state --jq .state) \
	|| [[ "$gh_status" != "OPEN" ]]; then
	# shellcheck disable=SC2154
	curl -L -X POST \
		--user "$GERRIT_BOT_USER:$GERRIT_BOT_HTTP_PASSWD" \
		--header "Content-Type: application/json" \
		--data "{'message': 'Issue #$gh_issue does not exist or is already closed.'}" \
		--fail-with-body \
		"$gerrit_url/$change_num/revisions/$patch_set/review"
	echo "Comment points to incorrect GitHub issue."
	exit 0
fi

# Get latest info about a change itself - first line is the XSSI mitigation string, drop it
curl -s -X GET \
	--user "$GERRIT_BOT_USER:$GERRIT_BOT_HTTP_PASSWD" \
	"$gerrit_url/spdk%2Fspdk~$change_num?$gerrit_format_q" \
	| tail -n +2 | jq . | tee change.json

# Do not test any change marked as WIP
# .work_in_progress is not set when false
work_in_progress="$(jq -r '.work_in_progress' change.json)"
if [[ "$work_in_progress" == "true" ]]; then
	echo "Ignore. Comment posted to WIP change."
	exit 0
fi

# Only test latest patch set
current_patch_set="$(jq -r '.current_revision_number' change.json)"
if ((current_patch_set != patch_set)); then
  echo "Ignore. Comment posted to different ($current_patch_set) patch set."
	exit 0
fi

# False positive should be used only on changes that already have a negative Verified vote
verified=$(jq -r ".labels.Verified.all[]? | select(.username==\"$GERRIT_BOT_USER\").value" change.json)
if ((verified != -1)); then
	echo "Ignore. Comment posted with no negative vote from CI."
	exit 0
fi

# Find workflow to rerun. As a sanity check grab comment meeting following criteria:
# most recent failed build comment posted by spdk-bot only on latest patch set
# NOTE: Message parsing is very fragile and has to match summary job
mapfile -t fp_run_failed_messages < <(
	jq -r ".messages[] |
		select(.author.username==\"$GERRIT_BOT_USER\") |
		select(._revision_number==$patch_set) |
		select(.message | test(\"Build failed\")) |
		.message" change.json | grep "Build failed. Results: "
)

if ((${#fp_run_failed_messages[@]} == 0)); then
  echo "Did not find comments indicating build failure"
  exit 1
fi

# E.g:
# Build failed. Results: [15028790454/1](https://github.com/spdk/spdk-ci/actions/runs/15028790454/attempts/3)
# Build failed. Results: [15028790454/1](https://github.com/spdk/spdk-ci/actions/runs/15028790454)
fp_run_failed_messages=("${fp_run_failed_messages[@]}")
# E.g: 15028790454
fp_run_id=${fp_run_failed_messages[-1]//@(*"runs/"|"/attempts"*)/}
# E.g: https://github.com/spdk/spdk-ci/actions/runs/15028790454
fp_run_url=${fp_run_failed_messages[-1]//@(*"("|")")/}

message="Another instance of this failure. Reported by @$reported_by. Log: $fp_run_url"
# Special PAT to read/write GH issues is required
GH_TOKEN=$GH_ISSUES_PAT gh issue -R "$spdk_repo" comment "$gh_issue" -b "$message"

# Rerun only failed jobs, which will rerun all dependent ones too.
gh run rerun "$fp_run_id" --failed -R "$GH_REPO"

# Reset the verified vote and leave a comment indicating that workflows were retriggered
curl -L -X POST  \
	--user "$GERRIT_BOT_USER:$GERRIT_BOT_HTTP_PASSWD" \
	--header "Content-Type: application/json" \
	--data "{'message': 'Retriggered', 'labels': {'Verified': '0'}}" \
	--fail-with-body \
	"$gerrit_url/$change_num/revisions/$patch_set/review"
