#!/usr/bin/env bash
# Print the common job matrix for GitHub step outputs.
#
# SELECTED_JOB holds the workflow_dispatch input. Reusable callers leave
# it empty and get the full matrix.
set -euo pipefail
shopt -s inherit_errexit

job=${SELECTED_JOB:-all}

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

jobs=$(jq -c --arg repository "$GITHUB_REPOSITORY" '
	map(. + {container_image: (if .guest then "ghcr.io/refenv/cijoe-docker:v0.9.54"
		else "ghcr.io/\($repository):fedora_43" end)})
' .github/common-jobs.json)

if [[ $job != all ]]; then
	jobs=$(jq -c --arg name "$job" 'map(select(.name == $name))' <<< "$jobs")
	if [[ $(jq length <<< "$jobs") != 1 ]]; then
		echo "Unknown common job: $job" >&2
		exit 1
	fi
fi

for distro in $(jq -r '[.[] | select(.needs_vm_image) | .distro] | unique | .[]' <<< "$jobs"); do
	vm=$(vm_image "$distro")
	jobs=$(jq -c --arg distro "$distro" --argjson vm "$vm" '
		map(if .needs_vm_image and .distro == $distro then . + ($vm // {}) else . end)
	' <<< "$jobs")
done

matrix=$(jq -c '{include: .}' <<< "$jobs")
echo "matrix=$matrix"
