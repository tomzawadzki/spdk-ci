#!/usr/bin/env bash
# Print the common job matrix for GitHub step outputs.
set -euo pipefail
shopt -s inherit_errexit

jobs=$(jq -c --arg repository "$GITHUB_REPOSITORY" '
	map(. + {container_image: (if .guest then "ghcr.io/refenv/cijoe-docker:v0.9.54"
		else "ghcr.io/\($repository):fedora_43" end)})
' .github/common-jobs.json)

matrix=$(jq -c '{include: .}' <<< "$jobs")
echo "matrix=$matrix"
