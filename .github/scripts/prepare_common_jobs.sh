#!/usr/bin/env bash
# Print the common job matrix for GitHub step outputs.
set -euo pipefail
shopt -s inherit_errexit

# Print the run holding the latest VM image of a distribution.
vm_image() {
	local run_id

	run_id=$(gh api --paginate "/repos/$GITHUB_REPOSITORY/actions/artifacts?name=vm-image-$1_x86_64" \
		-q '.artifacts |= sort_by(.updated_at)[-1] | .artifacts.workflow_run.id')
	if [[ -z $run_id ]]; then
		echo "No VM image artifact found for $1" >&2
		exit 1
	fi
	jq -cn --arg run_id "$run_id" '{vm_run_id: $run_id}'
}

jobs=$(jq -c --arg repository "$GITHUB_REPOSITORY" '
	map(. + {container_image: (if .guest then "ghcr.io/refenv/cijoe-docker:v0.9.54"
		else "ghcr.io/\($repository):fedora_43" end)})
' .github/common-jobs.json)

for distro in $(jq -r '[.[] | select(.needs_vm_image) | .distro] | unique | .[]' <<< "$jobs"); do
	vm=$(vm_image "$distro")
	jobs=$(jq -c --arg distro "$distro" --argjson vm "$vm" '
		map(if .needs_vm_image and .distro == $distro then . + $vm else . end)
	' <<< "$jobs")
done

matrix=$(jq -c '{include: .}' <<< "$jobs")
echo "matrix=$matrix"
