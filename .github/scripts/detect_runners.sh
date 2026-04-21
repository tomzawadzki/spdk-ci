#!/usr/bin/env bash
# Usage: detect_runners.sh <runners-json> [<other-workflows-running>]
#
# Probe the GitHub runners API and, per category in CATEGORIES, write
# to $GITHUB_OUTPUT:
#   shr_<category>_online - online runners with label "shr_<category>"
#   shr_<category>_idle   - runners still free after reserving slots for
#                           OTHER in-flight gerrit-webhook-handler runs
#
# Each concurrent workflow is assumed to use RUNNERS_PER_WORKFLOW[cat]
# runners. The runner is either already busy or the workflow did not
# yet get to use the runner, so it shows up as idle.
#
# Assumes shr_<cat> runners are consumed only by Gerrit started workflows,
# other triggers or workflows could overcommit.
#
# An empty <runners-json> emits zeros so dependent jobs skip cleanly when
# the API call fails or the token is unset.
#
# Adding a category: append to CATEGORIES. If a workflow claims more than
# one shr_<cat>, set RUNNERS_PER_WORKFLOW[<cat>] so concurrent workflow
# runs cannot overcommit the pool.

set -euo pipefail

CATEGORIES=(generic rdma)
declare -A RUNNERS_PER_WORKFLOW=(
  # Keep in sync with the number of shr_generic_rank matrix entries in
  # spdk-common-tests.yml; adding one without bumping this lets concurrent
  # gerrit-webhook-handler workflows overcommit shr_generic.
  [generic]=2
)

runners_json="${1:-}"
other_workflows_running="${2:-0}"
: "${GITHUB_OUTPUT:=/dev/stdout}"

# Count online, idle, and busy runners carrying the given label.
# Prints three space-separated integers: online idle busy.
count_runners() {
  local json="$1" label="$2"
  echo "$json" | jq -r --arg label "$label" '
    [.runners[] | select(any(.labels[]; .name == $label) and .status == "online")] as $online
    | [$online[] | select(.busy == false)] as $idle
    | [$online[] | select(.busy == true)]  as $busy
    | "\($online | length) \($idle | length) \($busy | length)"
  '
}

for category in "${CATEGORIES[@]}"; do
  runner_label="shr_${category}"
  runners_per_workflow=${RUNNERS_PER_WORKFLOW[$category]:-0}

  if [[ -z "$runners_json" ]]; then
    online=0
    idle=0
    busy=0
  else
    read -r online idle busy <<<"$(count_runners "$runners_json" "$runner_label")"
  fi

  reserved_by_others=$(( other_workflows_running * runners_per_workflow - busy ))
  (( reserved_by_others < 0 )) && reserved_by_others=0
  available=$(( idle - reserved_by_others ))
  (( available < 0 )) && available=0

  echo "detect_runners[${category}]:" \
       "online=${online} idle=${idle} busy=${busy}" \
       "other_workflows_running=${other_workflows_running}" \
       "runners_per_workflow=${runners_per_workflow}" \
       "reserved_by_others=${reserved_by_others}" \
       "available=${available}" >&2

  {
    echo "shr_${category}_online=${online}"
    echo "shr_${category}_idle=${available}"
  } >>"$GITHUB_OUTPUT"
done
