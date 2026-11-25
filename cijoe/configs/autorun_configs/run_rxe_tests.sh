#!/usr/bin/env bash
# Build SPDK with autorun.sh, then run each test script in RXE_TESTS of the
# config over Soft-RoCE with RXE_TEST_ARGS, RXE_TEST_REPEAT times. Exit with 1
# at the first run that fails or takes longer than RXE_TEST_TIMEOUT seconds.
set -euo pipefail

conf=$1
configs=$(dirname "$(readlink -f "$0")")

# With SPDK_RUN_FUNCTIONAL_TEST=0 in the config, autorun.sh only builds.
bash "$configs/run_autorun.sh" "$conf"

set -a
# shellcheck disable=SC1090
source "$conf"
set +a

cd /opt/spdk
scripts/setup.sh

for test in $RXE_TESTS; do
	for ((run = 1; run <= RXE_TEST_REPEAT; run++)); do
		echo "Running $test $RXE_TEST_ARGS, run $run of $RXE_TEST_REPEAT"
		# As in run_autorun.sh, the test writes to a file, so it cannot block
		# on CIJoe reading its output.
		log=/opt/output/$(basename "$test" .sh)-$run.log
		rc=0
		# shellcheck disable=SC2086
		timeout -k 10 "$RXE_TEST_TIMEOUT" bash "$configs/run_with_rxe.sh" "$test" $RXE_TEST_ARGS &> "$log" || rc=$?
		cat "$log"
		if ((rc == 124 || rc == 137)); then
			echo "FAILED: $test $RXE_TEST_ARGS, run $run did not end in $RXE_TEST_TIMEOUT s"
			exit 1
		elif ((rc != 0)); then
			echo "FAILED: $test $RXE_TEST_ARGS, run $run"
			exit 1
		fi
	done
done
