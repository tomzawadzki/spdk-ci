#!/usr/bin/env bash
# Runs a built stacks.jar in a Gerrit container and checks over REST that it
# loads, answers the operators and serves the menus. The tests in src/test
# cover what the operators return. This covers the jar itself, its manifest and
# its linkage against the Gerrit of the image, which may be newer than the one
# it was built for.
#
# Usage: smoke.sh <image> [jar]
#   ENGINE=podman smoke.sh docker.io/gerritcodereview/gerrit:3.14.4
set -euo pipefail

image=${1:?usage: smoke.sh <image> [jar]}
here=$(cd "$(dirname "$0")" && pwd)
jar=${2:-$here/target/stacks.jar}
engine=${ENGINE:-docker}
name=stacks-smoke-$$
url=

cleanup() {
	local rc=$?
	if [ $rc -ne 0 ]; then
		echo "--- error_log" >&2
		$engine exec "$name" tail -n 100 /var/gerrit/logs/error_log >&2 || $engine logs "$name" >&2 || true
	fi
	$engine rm -f "$name" > /dev/null 2>&1 || true
	exit $rc
}
trap cleanup EXIT

fail() {
	echo "FAIL: $*" >&2
	exit 1
}

# REST call as the admin the image creates, without the )]}' line.
rest() {
	local method=$1 path=$2
	shift 2
	curl -fsS -u admin:secret -X "$method" -H 'Content-Type: application/json' "$@" "$url/a$path" | sed 1d
}

# Replaces a file of a change with stdin and publishes it as a new patch set.
publish() {
	curl -fsS -u admin:secret -X PUT -H 'Content-Type: application/octet-stream' --data-binary @- \
		"$url/a/changes/$1/edit/$2"
	rest POST "/changes/$1/edit:publish" -d '{}' > /dev/null
}

approve() {
	rest POST "/changes/$1/revisions/current/review" -d '{"labels": {"Code-Review": 2}}' > /dev/null
}

expect() {
	local q=$1 want=$2 got
	got=$(rest GET "/changes/?q=$(jq -rn --arg q "project:smoke $q" '$q | @uri')" | jq -c '[.[]._number] | sort')
	[ "$got" = "$want" ] || fail "$q returned $got, expected $want"
	echo "ok: $q -> $got"
}

# The site is a volume that only exists once the container runs, so this sets
# up gerrit.config and the plugin and then hands over to the stock entrypoint.
# shellcheck disable=SC2016 # expands in the container
$engine create --name "$name" -p 127.0.0.1::8080 --entrypoint /bin/bash "$image" -c '
	set -e
	git config -f /var/gerrit/etc/gerrit.config change.mergeabilityComputationBehavior API_REF_UPDATED_AND_CHANGE_REINDEX
	cp /tmp/stacks.jar /var/gerrit/plugins/stacks.jar
	exec /entrypoint.sh' > /dev/null
$engine cp "$jar" "$name:/tmp/stacks.jar"
$engine start "$name" > /dev/null
url=http://$($engine port "$name" 8080 | head -n 1)

for _ in $(seq 1 180); do
	if curl -fsS -o /dev/null "$url/config/server/version" 2> /dev/null; then
		break
	fi
	sleep 1
done
echo "ok: $(curl -fsS "$url/config/server/version" | sed 1d) from $image"
rest GET /plugins/ | jq -e '.stacks' > /dev/null || fail "stacks is not loaded"

# A <- B with A approved, in a project that submits with REBASE_ALWAYS as
# spdk/spdk does. A new patch set of A drops its approval and leaves B on the
# old one, which only counts as merged once A is submitted.
rest PUT /projects/smoke -d '{"create_empty_commit": true, "submit_type": "REBASE_ALWAYS"}' > /dev/null
a=$(rest POST /changes/ -d '{"project": "smoke", "branch": "master", "subject": "A"}' | jq -r ._number)
b=$(rest POST /changes/ -d "{\"project\": \"smoke\", \"branch\": \"master\", \"subject\": \"B\", \"base_change\": \"$a\"}" | jq -r ._number)
approve "$a"
expect "stack_stacks:ready" "[$a]"
expect "behind_stacks:1w" "[]"
echo a | publish "$a" a.txt
expect "stack_stacks:broken" "[$b]"
approve "$a"
rest POST "/changes/$a/submit" -d '{}' > /dev/null
expect "stack_stacks:broken" "[]"

# The menus, from stacks.config submitted to refs/meta/config of All-Projects.
m=$(rest POST /changes/ -d '{"project": "All-Projects", "branch": "refs/meta/config", "subject": "Add stacks.config"}' | jq -r ._number)
publish "$m" stacks.config < "$here/stacks.config"
approve "$m"
rest POST "/changes/$m/submit" -d '{}' > /dev/null
# Other plugins add menus too, compare only those of stacks.config.
want=$(git config -f "$here/stacks.config" --get-regexp '^menu\..*\.item$' |
	sed -E 's/^menu\.(.*)\.item (.*)$/\1: \2/' | jq -Rsc 'split("\n")[:-1]')
got=$(rest GET /config/server/top-menus | jq -c --argjson want "$want" \
	'[.[] | .name as $m | .items[] | "\($m): \(.name)"] | map(select(IN($want[])))')
[ "$got" = "$want" ] || fail "the menus have $got, stacks.config has $want"
echo "ok: menu items $got"

if $engine exec "$name" grep -E 'LinkageError|NoSuchMethodError|NoSuchFieldError|NoClassDefFoundError|AbstractMethodError|IncompatibleClassChangeError' /var/gerrit/logs/error_log; then
	fail "linkage errors in error_log"
fi
echo "ok: no linkage errors"
