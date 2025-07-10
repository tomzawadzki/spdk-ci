#!/usr/bin/env bash
set -e
shopt -s nullglob extglob

distros_to_build=()
changed_files=()

# If triggered by workflow_dispatch, build the input distro selected by the user.
# It's a single choice input so we don't need to do any additional checks.
# If triggered by a push event, build all of the distros which may have been affected
# by the change.
# * .github/workflows/build_qcow2.yml - main workflow changed, build all supported distros
# * cijoe/configs/*.toml or cijoe/workflows/*.yaml - configration changed, add affected distro
#   to the list of distros to build.
if [[ "$GH_EVENT_NAME" == "workflow_dispatch" ]]; then
	distros_to_build+=("$GH_INPUTS_DISTRO")
else
	# shellcheck disable=2206
	changed_files+=($CHANGED_FILES)
fi

for file in "${changed_files[@]}"; do
	case "$file" in
		*build_qcow2.yml)
			workflows_distros=(cijoe/workflows/build_*_qcow2_using_qemu.yaml)
			workflows_distros=("${workflows_distros[@]#*build_}")
			workflows_distros=("${workflows_distros[@]%_qcow2*}")
			distros_to_build+=("${workflows_distros[@]}")
			;;
		*qemuhost-with-guest-*.toml|*build_*qcow2_using_qemu.yaml)
			distro=${file//@(*qemuhost-with-guest-|*build_)}
			distro=${distro//@(.toml|_qcow2_using_qemu.yaml)}
			distros_to_build+=("$distro")
			;;
		*) ;;
	esac
done

# If no distros were selected then something went really wrong here, as
# the "push" trigger is set to run on changes to the same files which
# are used in the case switch above.
if ((${#distros_to_build[@]} == 0)); then
	echo "No distros to build, exiting."
	exit 1
fi

# Prepere the output to be used by JSON parser in later workflow jobs.
mapfile -t _distros < <(printf '"%s"\n' "${distros_to_build[@]}" | sort -u)
joined_distros=$(IFS=","; echo "${_distros[*]}")
echo "distro=[$joined_distros]"
echo "distro=[$joined_distros]" >> "$GITHUB_OUTPUT"
