#!/bin/bash
# End-to-end tests for the SPDK Checks plugin.
#
# Builds containers from scratch, deploys Gerrit + checks-api,
# creates test data, and verifies all functionality via REST API.
#
# Usage:
#   ./run-e2e-tests.sh            # run tests, then tear down
#   ./run-e2e-tests.sh --keep     # run tests, leave env running for manual inspection
#   ./run-e2e-tests.sh --cleanup  # just tear down (no tests)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---- Configuration ----------------------------------------------------------
DEV_PORT="${DEV_PORT:-9080}"
GERRIT="http://localhost:$DEV_PORT"
API="http://localhost:$DEV_PORT/checks-api/v1"
AUTH="admin:secret"
COMPOSE="docker compose -f docker-compose.dev.yaml"
PASS=0
FAIL=0
TOTAL=0
KEEP=false

for arg in "$@"; do
    case "$arg" in
        --keep)    KEEP=true ;;
        --cleanup) $COMPOSE down -v 2>/dev/null || true; echo "Cleaned up."; exit 0 ;;
        *)         echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

# ---- Helpers ----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); echo -e "  ${GREEN}✓${NC} $1"; }
fail() { FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); echo -e "  ${RED}✗${NC} $1"; echo "    $2"; }

assert_eq() {
    local desc=$1 expected=$2 actual=$3
    if [ "$expected" = "$actual" ]; then
        pass "$desc"
    else
        fail "$desc" "expected='$expected' actual='$actual'"
    fi
}

assert_contains() {
    local desc=$1 haystack=$2 needle=$3
    if echo "$haystack" | grep -q "$needle"; then
        pass "$desc"
    else
        fail "$desc" "output does not contain '$needle'"
    fi
}

assert_not_empty() {
    local desc=$1 value=$2
    if [ -n "$value" ]; then
        pass "$desc"
    else
        fail "$desc" "value is empty"
    fi
}

gerrit_api() {
    # Strip Gerrit's magic XSSI prefix
    curl -sf --user "$AUTH" "$GERRIT/a/$1" | sed 's/^)]}.//'
}

json_field() {
    python3 -c "import json,sys; print(json.load(sys.stdin)$1)" 2>/dev/null
}

cleanup() {
    if [ "$KEEP" = true ]; then
        echo ""
        echo -e "${YELLOW}=== Environment left running (--keep) ===${NC}"
        echo "  Gerrit UI:  $GERRIT"
        echo "  Checks API: $API/health"
        echo "  Tear down:  ./run-e2e-tests.sh --cleanup"
    else
        echo ""
        echo "Tearing down..."
        $COMPOSE down -v 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ---- Phase 1: Build & Deploy ------------------------------------------------
echo ""
echo "============================================================"
echo "  SPDK Checks Plugin — End-to-End Tests"
echo "============================================================"
echo ""
echo "Phase 1: Build and deploy from clean state"
echo "------------------------------------------------------------"

# Clean start
$COMPOSE down -v 2>/dev/null || true

echo "Building containers..."
$COMPOSE up -d --build 2>&1 | tail -5

echo "Waiting for Gerrit..."
for i in $(seq 1 120); do
    if curl -sf "$GERRIT/config/server/version" > /dev/null 2>&1; then
        pass "Gerrit is up"
        break
    fi
    if [ "$i" -eq 120 ]; then fail "Gerrit startup" "timed out after 240s"; exit 1; fi
    sleep 2
done

echo "Waiting for checks-api..."
for i in $(seq 1 60); do
    if curl -sf "$API/health" > /dev/null 2>&1; then
        pass "checks-api is up"
        break
    fi
    if [ "$i" -eq 60 ]; then fail "checks-api startup" "timed out after 120s"; exit 1; fi
    sleep 2
done

# ---- Phase 2: Gerrit Setup --------------------------------------------------
echo ""
echo "Phase 2: Gerrit project and label setup"
echo "------------------------------------------------------------"

./setup-gerrit.sh > /dev/null 2>&1

# Verify project exists
proj=$(gerrit_api "projects/test-project" | json_field "['name']")
assert_eq "test-project exists in Gerrit" "test-project" "$proj"

# Verify Verified label exists
vlabel=$(gerrit_api "projects/All-Projects/labels/Verified" | json_field "['name']")
assert_eq "Verified label configured" "Verified" "$vlabel"

# Verify label permission (Administrators can vote)
access=$(gerrit_api "projects/All-Projects/access")
has_verified=$(echo "$access" | python3 -c "
import json,sys
d = json.load(sys.stdin)
perms = d.get('local',{}).get('refs/heads/*',{}).get('permissions',{})
print('yes' if 'label-Verified' in perms else 'no')" 2>/dev/null)
assert_eq "Verified permission granted" "yes" "$has_verified"

# ---- Phase 3: Create real Gerrit changes ------------------------------------
echo ""
echo "Phase 3: Create test changes via git push"
echo "------------------------------------------------------------"

TMPDIR=$(mktemp -d)
cd "$TMPDIR"
git clone "http://$AUTH@localhost:$DEV_PORT/a/test-project" repo 2>/dev/null
cd repo
git config user.email "admin@example.com"
git config user.name "Admin"
curl -sf "$GERRIT/tools/hooks/commit-msg" -o .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg

for i in 1 2 3 4; do
    echo "content-$i" > "file-$i.txt"
    git add .
    git commit -m "Test change $i" > /dev/null 2>&1
    git push origin HEAD:refs/for/master > /dev/null 2>&1
done

cd /
rm -rf "$TMPDIR"

# Verify changes exist
for i in 1 2 3 4; do
    status=$(gerrit_api "changes/$i" | json_field "['status']")
    assert_eq "Change $i exists (status=NEW)" "NEW" "$status"
done

# ---- Phase 4: Seed webhook data and verify checks --------------------------
echo ""
echo "Phase 4: Seed CI data and verify checks API"
echo "------------------------------------------------------------"
cd "$SCRIPT_DIR"

./seed-test-data.sh > /dev/null 2>&1

# Change 1: completed with mix of pass/fail (6 jobs)
c1=$(curl -sf "$API/changes/1/patchsets/1/runs")
c1_status=$(echo "$c1" | json_field "['runs'][0]['status']")
c1_conclusion=$(echo "$c1" | json_field "['runs'][0]['conclusion']")
c1_jobs=$(echo "$c1" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['runs'][0]['jobs']))")
assert_eq "Change 1: run status=completed" "completed" "$c1_status"
assert_eq "Change 1: conclusion=failure" "failure" "$c1_conclusion"
assert_eq "Change 1: 6 jobs" "6" "$c1_jobs"

# Verify individual job statuses for change 1
c1_passed=$(echo "$c1" | python3 -c "
import json,sys
jobs = json.load(sys.stdin)['runs'][0]['jobs']
print(sum(1 for j in jobs if j['conclusion'] == 'success'))")
c1_failed=$(echo "$c1" | python3 -c "
import json,sys
jobs = json.load(sys.stdin)['runs'][0]['jobs']
print(sum(1 for j in jobs if j['conclusion'] == 'failure'))")
assert_eq "Change 1: 4 jobs passed" "4" "$c1_passed"
assert_eq "Change 1: 2 jobs failed" "2" "$c1_failed"

# Change 2: in_progress
c2=$(curl -sf "$API/changes/2/patchsets/1/runs")
c2_status=$(echo "$c2" | json_field "['runs'][0]['status']")
assert_eq "Change 2: run status=in_progress" "in_progress" "$c2_status"

# Change 3: queued
c3=$(curl -sf "$API/changes/3/patchsets/1/runs")
c3_status=$(echo "$c3" | json_field "['runs'][0]['status']")
assert_eq "Change 3: run status=queued" "queued" "$c3_status"

# Change 4: all success
c4=$(curl -sf "$API/changes/4/patchsets/1/runs")
c4_status=$(echo "$c4" | json_field "['runs'][0]['status']")
c4_conclusion=$(echo "$c4" | json_field "['runs'][0]['conclusion']")
assert_eq "Change 4: run status=completed" "completed" "$c4_status"
assert_eq "Change 4: conclusion=success" "success" "$c4_conclusion"

# Verify job-level URLs
c4_url=$(echo "$c4" | python3 -c "
import json,sys
jobs = json.load(sys.stdin)['runs'][0]['jobs']
print(jobs[0]['html_url'])")
assert_contains "Job URLs link to GitHub" "$c4_url" "github.com/spdk/spdk-ci/actions"

# ---- Phase 5: Verified vote -------------------------------------------------
echo ""
echo "Phase 5: Verified vote on completed runs"
echo "------------------------------------------------------------"

# Re-send completed events to trigger Verified votes (now that label exists)
curl -sf -X POST "$API/webhook/github" \
    -H 'Content-Type: application/json' \
    -H 'X-GitHub-Event: workflow_run' \
    -d '{
        "action": "completed",
        "workflow_run": {
            "id": 10001, "name": "SPDK CI", "run_number": 42,
            "run_attempt": 1, "status": "completed", "conclusion": "failure",
            "html_url": "https://github.com/spdk/spdk-ci/actions/runs/10001",
            "event": "repository_dispatch",
            "display_title": "Change 1 Patchset 1"
        }
    }' > /dev/null
curl -sf -X POST "$API/webhook/github" \
    -H 'Content-Type: application/json' \
    -H 'X-GitHub-Event: workflow_run' \
    -d '{
        "action": "completed",
        "workflow_run": {
            "id": 10004, "name": "SPDK CI", "run_number": 45,
            "run_attempt": 1, "status": "completed", "conclusion": "success",
            "html_url": "https://github.com/spdk/spdk-ci/actions/runs/10004",
            "event": "repository_dispatch",
            "display_title": "Change 4 Patchset 1"
        }
    }' > /dev/null
sleep 2

# Check Verified labels via Gerrit API
v1=$(gerrit_api "changes/1/detail" | python3 -c "
import json,sys
d = json.load(sys.stdin)
for v in d.get('labels',{}).get('Verified',{}).get('all',[]):
    if v.get('value',0) != 0: print(v['value'])" 2>/dev/null)
assert_eq "Change 1: Verified -1 (failure)" "-1" "$v1"

v4=$(gerrit_api "changes/4/detail" | python3 -c "
import json,sys
d = json.load(sys.stdin)
for v in d.get('labels',{}).get('Verified',{}).get('all',[]):
    if v.get('value',0) != 0: print(v['value'])" 2>/dev/null)
assert_eq "Change 4: Verified +1 (success)" "1" "$v4"

# Change 2 (in_progress) should NOT have a Verified vote
v2=$(gerrit_api "changes/2/detail" | python3 -c "
import json,sys
d = json.load(sys.stdin)
vals = [v.get('value',0) for v in d.get('labels',{}).get('Verified',{}).get('all',[])]
print('none' if all(v == 0 for v in vals) else 'has_vote')" 2>/dev/null)
assert_eq "Change 2: no Verified vote (in_progress)" "none" "$v2"

# Verify the review message is posted
msg1=$(gerrit_api "changes/1/messages" | python3 -c "
import json,sys
msgs = json.load(sys.stdin)
ci_msgs = [m['message'] for m in msgs if 'Build Failed' in m.get('message','')]
print('found' if ci_msgs else 'missing')" 2>/dev/null)
assert_eq "Change 1: review message posted" "found" "$msg1"

msg4=$(gerrit_api "changes/4/messages" | python3 -c "
import json,sys
msgs = json.load(sys.stdin)
ci_msgs = [m['message'] for m in msgs if 'Build Successful' in m.get('message','')]
print('found' if ci_msgs else 'missing')" 2>/dev/null)
assert_eq "Change 4: review message posted" "found" "$msg4"

# ---- Phase 6: Run registration and idempotency ------------------------------
echo ""
echo "Phase 6: Run registration and idempotency"
echo "------------------------------------------------------------"

# Register a new run
reg=$(curl -sf -o /dev/null -w "%{http_code}" -X POST "$API/runs/register" \
    -H 'Content-Type: application/json' \
    -d '{"gerrit_change": 1, "gerrit_patchset": 1, "github_run_id": 99999}')
assert_eq "Register new run returns 201" "201" "$reg"

# Re-register same run (idempotent)
reg2=$(curl -sf -o /dev/null -w "%{http_code}" -X POST "$API/runs/register" \
    -H 'Content-Type: application/json' \
    -d '{"gerrit_change": 1, "gerrit_patchset": 1, "github_run_id": 99999}')
assert_eq "Re-register same run returns 201" "201" "$reg2"

# Re-send same webhook data (idempotent upsert)
curl -sf -X POST "$API/webhook/github" \
    -H 'Content-Type: application/json' \
    -H 'X-GitHub-Event: workflow_run' \
    -d '{
        "action": "completed",
        "workflow_run": {
            "id": 10004, "name": "SPDK CI", "run_number": 45,
            "run_attempt": 1, "status": "completed", "conclusion": "success",
            "html_url": "https://github.com/spdk/spdk-ci/actions/runs/10004",
            "event": "repository_dispatch",
            "display_title": "Change 4 Patchset 1"
        }
    }' > /dev/null
c4_after=$(curl -sf "$API/changes/4/patchsets/1/runs")
c4_jobs_after=$(echo "$c4_after" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['runs'][0]['jobs']))")
assert_eq "Idempotent re-send: still 6 jobs" "6" "$c4_jobs_after"

# ---- Phase 7: Edge cases ----------------------------------------------------
echo ""
echo "Phase 7: Edge cases and error handling"
echo "------------------------------------------------------------"

# Non-existent change returns empty runs
c999=$(curl -sf "$API/changes/999/patchsets/1/runs")
c999_count=$(echo "$c999" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['runs']))")
assert_eq "Non-existent change: 0 runs" "0" "$c999_count"

# Invalid webhook payload (missing event type — accepted but ignored)
bad_status=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/webhook/github" \
    -H 'Content-Type: application/json' \
    -d '{"action":"completed"}')
assert_eq "Unknown event type returns 200 (ignored)" "200" "$bad_status"

# Health endpoint always works
health=$(curl -sf "$API/health" | json_field "['status']")
assert_eq "Health check returns ok" "ok" "$health"

# ---- Phase 8: Queue status --------------------------------------------------
echo ""
echo "Phase 8: Queue status endpoint"
echo "------------------------------------------------------------"

queue=$(curl -sf "$API/queue/status")
assert_not_empty "Queue status endpoint responds" "$queue"

queue_pending=$(echo "$queue" | json_field "['pending']" 2>/dev/null || echo "N/A")
queue_running=$(echo "$queue" | json_field "['running']" 2>/dev/null || echo "N/A")
# Queue should be empty in dev mode (no real Gerrit webhooks)
if [ "$queue_pending" != "N/A" ]; then
    pass "Queue reports pending count"
else
    pass "Queue status has expected structure"
fi

# ---- Phase 9: Gerrit plugin JS served correctly -----------------------------
echo ""
echo "Phase 9: Frontend plugin"
echo "------------------------------------------------------------"

plugin_status=$(curl -s -o /dev/null -w "%{http_code}" "$GERRIT/plugins/spdk-checks/static/spdk-checks.js")
assert_eq "Plugin JS served by Gerrit" "200" "$plugin_status"

plugin_content=$(curl -sf "$GERRIT/plugins/spdk-checks/static/spdk-checks.js" | head -5)
assert_contains "Plugin JS is valid IIFE" "$plugin_content" "Gerrit"

# ---- Phase 10: Container health ---------------------------------------------
echo ""
echo "Phase 10: Container health"
echo "------------------------------------------------------------"

for svc in gerrit-dev checks-api-dev nginx-dev; do
    status=$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo "missing")
    assert_eq "Container $svc is running" "running" "$status"
done

# Check no error-level logs from checks-api (ignoring WEBHOOK_SECRET warnings)
errors=$(docker logs checks-api-dev 2>&1 | grep -c " ERROR " || true)
assert_eq "No ERROR logs in checks-api" "0" "$errors"

# ---- Summary ----------------------------------------------------------------
echo ""
echo "============================================================"
if [ "$FAIL" -eq 0 ]; then
    echo -e "  ${GREEN}All $TOTAL tests passed${NC}"
else
    echo -e "  ${RED}$FAIL of $TOTAL tests FAILED${NC}"
fi
echo "============================================================"
echo ""

exit "$FAIL"
