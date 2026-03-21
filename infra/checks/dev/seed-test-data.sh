#!/bin/bash
# Seed test data into the checks-api database via its REST API.
# Idempotent: safe to run multiple times (the backend uses upsert).
set -euo pipefail

API="http://localhost:8080/checks-api/v1"

# --- helpers ----------------------------------------------------------------
wait_healthy() {
    echo "Waiting for checks-api to be healthy..."
    for i in $(seq 1 60); do
        if curl -sf "$API/health" > /dev/null 2>&1; then
            echo "checks-api is healthy."
            return
        fi
        sleep 2
    done
    echo "ERROR: checks-api did not become healthy in time." >&2
    exit 1
}

register_run() {
    local change=$1 patchset=$2 run_id=$3
    curl -sf -X POST "$API/runs/register" \
        -H 'Content-Type: application/json' \
        -d "{\"gerrit_change\": $change, \"gerrit_patchset\": $patchset, \"github_run_id\": $run_id}" \
        > /dev/null
}

send_workflow_run() {
    local run_id=$1 name=$2 run_number=$3 status=$4 conclusion=$5 title=$6
    curl -sf -X POST "$API/webhook/github" \
        -H 'Content-Type: application/json' \
        -H 'X-GitHub-Event: workflow_run' \
        -d "{
            \"action\": \"$( [ \"$status\" = completed ] && echo completed || echo in_progress )\",
            \"workflow_run\": {
                \"id\": $run_id,
                \"name\": \"$name\",
                \"run_number\": $run_number,
                \"run_attempt\": 1,
                \"status\": \"$status\",
                \"conclusion\": $( [ -z "$conclusion" ] && echo null || echo "\"$conclusion\"" ),
                \"html_url\": \"https://github.com/spdk/spdk-ci/actions/runs/$run_id\",
                \"event\": \"repository_dispatch\",
                \"display_title\": \"$title\"
            }
        }" > /dev/null
}

send_workflow_job() {
    local job_id=$1 run_id=$2 name=$3 status=$4 conclusion=$5 started=$6 completed=$7
    curl -sf -X POST "$API/webhook/github" \
        -H 'Content-Type: application/json' \
        -H 'X-GitHub-Event: workflow_job' \
        -d "{
            \"action\": \"$( [ \"$status\" = completed ] && echo completed || echo "$status" )\",
            \"workflow_job\": {
                \"id\": $job_id,
                \"run_id\": $run_id,
                \"name\": \"$name\",
                \"status\": \"$status\",
                \"conclusion\": $( [ -z "$conclusion" ] && echo null || echo "\"$conclusion\"" ),
                \"html_url\": \"https://github.com/spdk/spdk-ci/actions/runs/$run_id/job/$job_id\",
                \"started_at\": $( [ -z "$started" ] && echo null || echo "\"$started\"" ),
                \"completed_at\": $( [ -z "$completed" ] && echo null || echo "\"$completed\"" ),
                \"runner_name\": \"ubuntu-latest\"
            }
        }" > /dev/null
}

# --- wait -------------------------------------------------------------------
wait_healthy

echo ""
echo "=== Seeding test data ==="

# ============================================================================
# Change 1, PS 1 — Completed run, mix of success/failure
# ============================================================================
echo "Change 1 PS 1: completed run (mix of pass/fail)..."
register_run 1 1 10001
send_workflow_run 10001 "SPDK CI" 42 completed failure "Change 1 Patchset 1"
send_workflow_job 20001 10001 "Build (gcc)"         completed success  "2024-01-15T10:00:00Z" "2024-01-15T10:05:00Z"
send_workflow_job 20002 10001 "Build (clang)"       completed success  "2024-01-15T10:00:00Z" "2024-01-15T10:06:00Z"
send_workflow_job 20003 10001 "Unit Tests"          completed success  "2024-01-15T10:06:00Z" "2024-01-15T10:12:00Z"
send_workflow_job 20004 10001 "Functional Tests"    completed failure  "2024-01-15T10:06:00Z" "2024-01-15T10:18:00Z"
send_workflow_job 20005 10001 "RDMA Tests"          completed failure  "2024-01-15T10:06:00Z" "2024-01-15T10:20:00Z"
send_workflow_job 20006 10001 "Lint Check"          completed success  "2024-01-15T10:00:00Z" "2024-01-15T10:02:00Z"

# ============================================================================
# Change 2, PS 1 — All jobs running (in_progress)
# ============================================================================
echo "Change 2 PS 1: all jobs in_progress..."
register_run 2 1 10002
send_workflow_run 10002 "SPDK CI" 43 in_progress "" "Change 2 Patchset 1"
send_workflow_job 20011 10002 "Build (gcc)"         in_progress ""     "2024-01-15T11:00:00Z" ""
send_workflow_job 20012 10002 "Build (clang)"       in_progress ""     "2024-01-15T11:00:00Z" ""
send_workflow_job 20013 10002 "Unit Tests"          in_progress ""     "2024-01-15T11:02:00Z" ""
send_workflow_job 20014 10002 "Functional Tests"    in_progress ""     "2024-01-15T11:02:00Z" ""
send_workflow_job 20015 10002 "RDMA Tests"          in_progress ""     "2024-01-15T11:02:00Z" ""
send_workflow_job 20016 10002 "Lint Check"          in_progress ""     "2024-01-15T11:00:00Z" ""

# ============================================================================
# Change 3, PS 1 — All jobs queued
# ============================================================================
echo "Change 3 PS 1: all jobs queued..."
register_run 3 1 10003
send_workflow_run 10003 "SPDK CI" 44 queued "" "Change 3 Patchset 1"
send_workflow_job 20021 10003 "Build (gcc)"         queued ""          "" ""
send_workflow_job 20022 10003 "Build (clang)"       queued ""          "" ""
send_workflow_job 20023 10003 "Unit Tests"          queued ""          "" ""
send_workflow_job 20024 10003 "Functional Tests"    queued ""          "" ""
send_workflow_job 20025 10003 "RDMA Tests"          queued ""          "" ""
send_workflow_job 20026 10003 "Lint Check"          queued ""          "" ""

# ============================================================================
# Change 4, PS 1 — All jobs passed (success)
# ============================================================================
echo "Change 4 PS 1: all jobs passed..."
register_run 4 1 10004
send_workflow_run 10004 "SPDK CI" 45 completed success "Change 4 Patchset 1"
send_workflow_job 20031 10004 "Build (gcc)"         completed success  "2024-01-15T12:00:00Z" "2024-01-15T12:05:00Z"
send_workflow_job 20032 10004 "Build (clang)"       completed success  "2024-01-15T12:00:00Z" "2024-01-15T12:06:00Z"
send_workflow_job 20033 10004 "Unit Tests"          completed success  "2024-01-15T12:06:00Z" "2024-01-15T12:12:00Z"
send_workflow_job 20034 10004 "Functional Tests"    completed success  "2024-01-15T12:06:00Z" "2024-01-15T12:18:00Z"
send_workflow_job 20035 10004 "RDMA Tests"          completed success  "2024-01-15T12:06:00Z" "2024-01-15T12:20:00Z"
send_workflow_job 20036 10004 "Lint Check"          completed success  "2024-01-15T12:00:00Z" "2024-01-15T12:02:00Z"

echo ""
echo "=== Test data seeded ==="
echo "  Change 1 PS 1: completed (mix pass/fail) — 6 jobs"
echo "  Change 2 PS 1: in_progress              — 6 jobs"
echo "  Change 3 PS 1: queued                   — 6 jobs"
echo "  Change 4 PS 1: completed (all pass)     — 6 jobs"
