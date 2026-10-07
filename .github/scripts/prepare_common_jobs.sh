#!/usr/bin/env bash
# Print the common job matrix and its scheduling for GitHub step outputs.
#
# SELECTED_JOB, REPEAT, MAX_PARALLEL and FAIL_FAST hold workflow_dispatch
# inputs. Reusable callers leave them empty and get the full matrix.
set -euo pipefail
shopt -s inherit_errexit

job=${SELECTED_JOB:-all}
repeat=${REPEAT:-1}
max_parallel=${MAX_PARALLEL:-10}
fail_fast=${FAIL_FAST:-true}

# Print the newest unexpired artifact of a distribution's VM image, or null.
vm_image() {
	local name="vm-image-$1_x86_64" pages

	# Read every page before choosing, so the result cannot depend on paging.
	pages=$(gh api --paginate --slurp \
		"/repos/$GITHUB_REPOSITORY/actions/artifacts?name=$name&per_page=100")
	jq -c --arg name "$name" '
		[.[].artifacts[] | select(.name == $name and .expired == false and .workflow_run.id != null)]
		| max_by([.created_at, .id])
		| if . then {vm_artifact_id: (.id | tostring), vm_run_id: (.workflow_run.id | tostring)} else null end
	' <<< "$pages"
}

whole_number() {
	[[ $1 =~ ^[0-9]{1,3}$ ]] && ((10#$1 >= $2 && 10#$1 <= $3))
}

if ! whole_number "$repeat" 1 256; then
	echo "repeat must be a whole number from 1 to 256" >&2
	exit 1
fi
if [[ $fail_fast != true && $fail_fast != false ]]; then
	echo "fail_fast must be true or false" >&2
	exit 1
fi

jobs=$(jq -c --arg repository "$GITHUB_REPOSITORY" '
	map(. + {container_image: (if .guest then "ghcr.io/refenv/cijoe-docker:v0.9.54"
		else "ghcr.io/\($repository):fedora_43" end)})
' .github/common-jobs.json)

if [[ $job == all ]]; then
	if ((10#$repeat != 1)); then
		echo "Repeating jobs requires selecting one common job" >&2
		exit 1
	fi
	# The full matrix keeps its unthrottled scheduling.
	max_parallel=256
else
	jobs=$(jq -c --arg name "$job" 'map(select(.name == $name))' <<< "$jobs")
	if [[ $(jq length <<< "$jobs") != 1 ]]; then
		echo "Unknown common job: $job" >&2
		exit 1
	fi
	if ! whole_number "$max_parallel" 1 20; then
		echo "max_parallel must be a whole number from 1 to 20" >&2
		exit 1
	fi
	max_parallel=$((10#$max_parallel))

	# A tag can move during a long run, so selected runs use the digest seen now.
	image=$(jq -r '.[0].container_image' <<< "$jobs")
	manifest=$(docker buildx imagetools inspect "$image" --format '{{json .Manifest}}')
	if ! digest=$(jq -er '.digest | select(. != null and . != "")' <<< "$manifest"); then
		echo "No digest found for $image" >&2
		exit 1
	fi
	jobs=$(jq -c --arg image "$image@$digest" 'map(. + {container_image: $image})' <<< "$jobs")
fi

for distro in $(jq -r '[.[] | select(.needs_vm_image) | .distro] | unique | .[]' <<< "$jobs"); do
	vm=$(vm_image "$distro")
	# Selected runs skip the cache, so they need an artifact.
	if [[ $vm == null && $job != all ]]; then
		echo "No unexpired VM artifact available for $job ($distro)" >&2
		exit 1
	fi
	jobs=$(jq -c --arg distro "$distro" --argjson vm "$vm" '
		map(if .needs_vm_image and .distro == $distro then . + ($vm // {}) else . end)
	' <<< "$jobs")
done

if [[ $job != all ]]; then
	jobs=$(jq -c --argjson count "$((10#$repeat))" '
		.[0] as $job | [range(1; $count + 1) as $sample | $job + {sample: $sample}]
	' <<< "$jobs")
fi

matrix=$(jq -c '{include: .}' <<< "$jobs")
echo "matrix=$matrix"
echo "max_parallel=$max_parallel"
echo "fail_fast=$fail_fast"
