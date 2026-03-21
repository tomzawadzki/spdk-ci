"""Functional tests for the SPDK Checks backend.

These tests run against a real HTTP server (uvicorn) backed by a temporary
SQLite database.  GitHub API calls are NOT made — the tests exercise only the
paths that are local to the checks backend (webhook ingestion, run/job CRUD,
validation, registration, etc.).

Usage:
    # Run from the checks directory:
    python3 -m pytest tests/test_functional.py -v

    # Or via the run_tests.sh wrapper which includes unit + functional tests.

The tests start a real uvicorn server on a random available port in a
subprocess, so they work outside of Docker as well.
"""

import hashlib
import hmac
import json
import os
import signal
import socket
import subprocess
import sys
import time

import pytest
import requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHECKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFRA_DIR = os.path.dirname(CHECKS_DIR)


def _free_port():
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(base_url, timeout=15):
    """Block until the health endpoint responds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{base_url}/checks-api/v1/health", timeout=2)
            if r.status_code == 200:
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"Server at {base_url} did not start within {timeout}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Start a real uvicorn server for the module and tear it down after."""
    port = _free_port()
    db_path = str(tmp_path_factory.mktemp("functional") / "checks.db")
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env.update({
        "CHECKS_DATABASE_PATH": db_path,
        "CHECKS_GITHUB_TOKEN": "fake-functional-test-token",
        "CHECKS_GITHUB_WEBHOOK_SECRET": "test-webhook-secret",
        "CHECKS_API_KEY": "",
        "GERRIT_URL": "http://127.0.0.1:1",  # port 1 = connection refused (fast fail)
        "LOG_LEVEL": "DEBUG",
    })

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=CHECKS_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_server(base_url)
        yield {"url": base_url, "port": port, "db_path": db_path, "proc": proc}
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)


@pytest.fixture()
def base_url(server):
    return server["url"]


def _api(base_url, path):
    return f"{base_url}/checks-api/v1{path}"


def _sign(body: bytes, secret: str = "test-webhook-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post_webhook(base_url, event, payload, secret="test-webhook-secret"):
    body = json.dumps(payload).encode()
    sig = _sign(body, secret)
    return requests.post(
        _api(base_url, "/webhook/github"),
        data=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": event,
            "Content-Type": "application/json",
        },
        timeout=5,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, base_url):
        r = requests.get(_api(base_url, "/health"), timeout=5)
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Registration + GET runs
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_run(self, base_url):
        r = requests.post(
            _api(base_url, "/runs/register"),
            json={
                "gerrit_change": 1001,
                "gerrit_patchset": 1,
                "gerrit_project": "spdk/spdk",
                "github_run_id": 90001,
            },
            timeout=5,
        )
        assert r.status_code == 201
        data = r.json()
        assert data["message"] == "Run registered"
        assert data["github_run_id"] == 90001

    def test_register_invalid_change(self, base_url):
        r = requests.post(
            _api(base_url, "/runs/register"),
            json={
                "gerrit_change": -1,
                "gerrit_patchset": 1,
                "github_run_id": 1,
            },
            timeout=5,
        )
        assert r.status_code == 400

    def test_register_invalid_patchset(self, base_url):
        r = requests.post(
            _api(base_url, "/runs/register"),
            json={
                "gerrit_change": 1,
                "gerrit_patchset": 0,
                "github_run_id": 1,
            },
            timeout=5,
        )
        assert r.status_code == 400

    def test_register_idempotent(self, base_url):
        """Registering the same run_id twice updates rather than duplicates."""
        for _ in range(2):
            r = requests.post(
                _api(base_url, "/runs/register"),
                json={
                    "gerrit_change": 1002,
                    "gerrit_patchset": 1,
                    "gerrit_project": "spdk/spdk",
                    "github_run_id": 90002,
                },
                timeout=5,
            )
            assert r.status_code == 201

        runs = requests.get(
            _api(base_url, "/changes/1002/patchsets/1/runs"), timeout=5
        ).json()["runs"]
        assert len(runs) == 1

    def test_get_runs_empty(self, base_url):
        r = requests.get(
            _api(base_url, "/changes/99999/patchsets/1/runs"), timeout=5
        )
        assert r.status_code == 200
        assert r.json() == {"runs": []}

    def test_get_runs_invalid_params(self, base_url):
        r = requests.get(
            _api(base_url, "/changes/-1/patchsets/1/runs"), timeout=5
        )
        assert r.status_code == 400

        r = requests.get(
            _api(base_url, "/changes/1/patchsets/0/runs"), timeout=5
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Webhook ingestion — full lifecycle
# ---------------------------------------------------------------------------

class TestWebhookLifecycle:
    """Simulates the full GitHub Actions webhook lifecycle."""

    CHANGE = 2001
    PATCHSET = 3
    RUN_ID = 80001
    JOB_IDS = [80101, 80102, 80103]

    @pytest.fixture(autouse=True)
    def _setup_run(self, base_url):
        """Register a run so webhooks can find it."""
        requests.post(
            _api(base_url, "/runs/register"),
            json={
                "gerrit_change": self.CHANGE,
                "gerrit_patchset": self.PATCHSET,
                "gerrit_project": "spdk/spdk",
                "github_run_id": self.RUN_ID,
            },
            timeout=5,
        )

    def test_workflow_run_in_progress(self, base_url):
        r = _post_webhook(base_url, "workflow_run", {
            "action": "in_progress",
            "workflow_run": {
                "id": self.RUN_ID,
                "name": "CI",
                "status": "in_progress",
                "conclusion": None,
                "html_url": f"https://github.com/runs/{self.RUN_ID}",
                "run_number": 42,
                "run_attempt": 1,
                "display_title": "CI",
                "head_branch": f"refs/changes/{self.CHANGE % 100:02d}/{self.CHANGE}/{self.PATCHSET}",
                "head_commit": None,
                "event": "repository_dispatch",
            },
        })
        assert r.status_code == 200

        # Verify run status via GET
        runs = requests.get(
            _api(base_url, f"/changes/{self.CHANGE}/patchsets/{self.PATCHSET}/runs"),
            timeout=5,
        ).json()["runs"]
        assert len(runs) >= 1
        run = next(r for r in runs if r["github_run_id"] == self.RUN_ID)
        assert run["status"] == "in_progress"

    def test_workflow_jobs_queued_and_running(self, base_url):
        # Post workflow_run first
        _post_webhook(base_url, "workflow_run", {
            "action": "in_progress",
            "workflow_run": {
                "id": self.RUN_ID,
                "name": "CI",
                "status": "in_progress",
                "conclusion": None,
                "html_url": f"https://github.com/runs/{self.RUN_ID}",
                "run_number": 42,
                "run_attempt": 1,
                "display_title": "CI",
                "head_branch": f"refs/changes/{self.CHANGE % 100:02d}/{self.CHANGE}/{self.PATCHSET}",
                "head_commit": None,
                "event": "repository_dispatch",
            },
        })

        # Three jobs: first queued, second in_progress, third completed
        for i, (job_id, status, conclusion) in enumerate([
            (self.JOB_IDS[0], "queued", None),
            (self.JOB_IDS[1], "in_progress", None),
            (self.JOB_IDS[2], "completed", "success"),
        ]):
            r = _post_webhook(base_url, "workflow_job", {
                "action": status if status != "completed" else "completed",
                "workflow_job": {
                    "id": job_id,
                    "run_id": self.RUN_ID,
                    "name": f"job-{i}",
                    "status": status,
                    "conclusion": conclusion,
                    "html_url": f"https://github.com/jobs/{job_id}",
                    "started_at": "2025-01-01T00:00:00Z",
                    "completed_at": "2025-01-01T00:10:00Z" if status == "completed" else None,
                    "steps": [],
                },
            })
            assert r.status_code == 200

        # Verify all jobs appear
        runs = requests.get(
            _api(base_url, f"/changes/{self.CHANGE}/patchsets/{self.PATCHSET}/runs"),
            timeout=5,
        ).json()["runs"]
        run = next(r for r in runs if r["github_run_id"] == self.RUN_ID)
        jobs = run["jobs"]
        assert len(jobs) == 3

        by_name = {j["name"]: j for j in jobs}
        assert by_name["job-0"]["status"] == "queued"
        assert by_name["job-1"]["status"] == "in_progress"
        assert by_name["job-2"]["status"] == "completed"
        assert by_name["job-2"]["conclusion"] == "success"

    def test_workflow_run_completed(self, base_url):
        # Register, start, complete
        _post_webhook(base_url, "workflow_run", {
            "action": "in_progress",
            "workflow_run": {
                "id": self.RUN_ID,
                "name": "CI",
                "status": "in_progress",
                "conclusion": None,
                "html_url": f"https://github.com/runs/{self.RUN_ID}",
                "run_number": 42,
                "run_attempt": 1,
                "display_title": "CI",
                "head_branch": f"refs/changes/{self.CHANGE % 100:02d}/{self.CHANGE}/{self.PATCHSET}",
                "head_commit": None,
                "event": "repository_dispatch",
            },
        })

        r = _post_webhook(base_url, "workflow_run", {
            "action": "completed",
            "workflow_run": {
                "id": self.RUN_ID,
                "name": "CI",
                "status": "completed",
                "conclusion": "failure",
                "html_url": f"https://github.com/runs/{self.RUN_ID}",
                "run_number": 42,
                "run_attempt": 1,
                "display_title": "CI",
                "head_branch": f"refs/changes/{self.CHANGE % 100:02d}/{self.CHANGE}/{self.PATCHSET}",
                "head_commit": None,
                "event": "repository_dispatch",
            },
        })
        assert r.status_code == 200

        runs = requests.get(
            _api(base_url, f"/changes/{self.CHANGE}/patchsets/{self.PATCHSET}/runs"),
            timeout=5,
        ).json()["runs"]
        run = next(r for r in runs if r["github_run_id"] == self.RUN_ID)
        assert run["status"] == "completed"
        assert run["conclusion"] == "failure"

    def test_unknown_webhook_event_ignored(self, base_url):
        r = _post_webhook(base_url, "star", {"action": "created"})
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Webhook signature validation
# ---------------------------------------------------------------------------

class TestWebhookSecurity:
    def test_invalid_signature_rejected(self, base_url):
        body = json.dumps({"action": "requested"}).encode()
        r = requests.post(
            _api(base_url, "/webhook/github"),
            data=body,
            headers={
                "X-Hub-Signature-256": "sha256=invalid",
                "X-GitHub-Event": "workflow_run",
                "Content-Type": "application/json",
            },
            timeout=5,
        )
        assert r.status_code == 401

    def test_missing_signature_rejected(self, base_url):
        body = json.dumps({"action": "requested"}).encode()
        r = requests.post(
            _api(base_url, "/webhook/github"),
            data=body,
            headers={
                "X-GitHub-Event": "workflow_run",
                "Content-Type": "application/json",
            },
            timeout=5,
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Cross-change isolation
# ---------------------------------------------------------------------------

class TestIsolation:
    """Runs for different changes don't bleed into each other."""

    def test_cross_change_isolation(self, base_url):
        # Register two different changes
        for change, run_id in [(3001, 70001), (3002, 70002)]:
            requests.post(
                _api(base_url, "/runs/register"),
                json={
                    "gerrit_change": change,
                    "gerrit_patchset": 1,
                    "github_run_id": run_id,
                },
                timeout=5,
            )

        # Each change should only see its own run
        for change, expected_run in [(3001, 70001), (3002, 70002)]:
            runs = requests.get(
                _api(base_url, f"/changes/{change}/patchsets/1/runs"),
                timeout=5,
            ).json()["runs"]
            assert len(runs) == 1
            assert runs[0]["github_run_id"] == expected_run

    def test_cross_patchset_isolation(self, base_url):
        """Different patchsets of the same change are separate."""
        for ps, run_id in [(1, 70011), (2, 70012)]:
            requests.post(
                _api(base_url, "/runs/register"),
                json={
                    "gerrit_change": 3003,
                    "gerrit_patchset": ps,
                    "github_run_id": run_id,
                },
                timeout=5,
            )

        runs_ps1 = requests.get(
            _api(base_url, "/changes/3003/patchsets/1/runs"), timeout=5
        ).json()["runs"]
        runs_ps2 = requests.get(
            _api(base_url, "/changes/3003/patchsets/2/runs"), timeout=5
        ).json()["runs"]
        assert len(runs_ps1) == 1
        assert runs_ps1[0]["github_run_id"] == 70011
        assert len(runs_ps2) == 1
        assert runs_ps2[0]["github_run_id"] == 70012


# ---------------------------------------------------------------------------
# Trigger + Rerun (expected to fail without real Gerrit/GitHub)
# ---------------------------------------------------------------------------

class TestTriggerRerun:
    """Trigger and rerun hit external services — verify proper error handling."""

    def test_trigger_no_gerrit(self, base_url):
        """Without a running Gerrit, trigger should return 502."""
        r = requests.post(
            _api(base_url, "/changes/99999/patchsets/1/trigger"),
            timeout=15,
        )
        assert r.status_code == 502

    def test_rerun_no_runs(self, base_url):
        """Rerun with no registered run returns 404."""
        r = requests.post(
            _api(base_url, "/changes/88888/patchsets/1/rerun"),
            timeout=5,
        )
        assert r.status_code == 404

    def test_trigger_invalid_params(self, base_url):
        r = requests.post(
            _api(base_url, "/changes/-1/patchsets/1/trigger"),
            timeout=5,
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Multiple runs per change (e.g., retries)
# ---------------------------------------------------------------------------

class TestMultipleRuns:
    def test_multiple_runs_per_patchset(self, base_url):
        """A patchset can have multiple workflow runs (e.g., manual retrigger)."""
        for run_id in [60001, 60002, 60003]:
            requests.post(
                _api(base_url, "/runs/register"),
                json={
                    "gerrit_change": 4001,
                    "gerrit_patchset": 1,
                    "github_run_id": run_id,
                },
                timeout=5,
            )

        runs = requests.get(
            _api(base_url, "/changes/4001/patchsets/1/runs"), timeout=5
        ).json()["runs"]
        assert len(runs) == 3
        run_ids = {r["github_run_id"] for r in runs}
        assert run_ids == {60001, 60002, 60003}


# ---------------------------------------------------------------------------
# Webhook with unregistered run (auto-discovery)
# ---------------------------------------------------------------------------

class TestWebhookAutoDiscovery:
    """Webhooks for runs not pre-registered via /register should be handled
    gracefully.  The backend should log a warning but not crash."""

    def test_workflow_run_unknown_change(self, base_url):
        r = _post_webhook(base_url, "workflow_run", {
            "action": "completed",
            "workflow_run": {
                "id": 99999,
                "name": "Unknown CI",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/runs/99999",
                "run_number": 1,
                "run_attempt": 1,
                "display_title": "Unknown",
                "head_branch": "main",
                "head_commit": None,
                "event": "push",
            },
        })
        # Should succeed (200) but log that it couldn't find a registered run
        assert r.status_code == 200

    def test_workflow_job_unknown_run(self, base_url):
        r = _post_webhook(base_url, "workflow_job", {
            "action": "completed",
            "workflow_job": {
                "id": 99998,
                "run_id": 99997,
                "name": "orphan-job",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://github.com/jobs/99998",
                "started_at": "2025-01-01T00:00:00Z",
                "completed_at": "2025-01-01T00:10:00Z",
                "steps": [],
            },
        })
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Job update lifecycle (queued → in_progress → completed)
# ---------------------------------------------------------------------------

class TestJobLifecycle:
    """Verify job status transitions via sequential webhooks."""

    CHANGE = 5001
    RUN_ID = 50001
    JOB_ID = 50101

    @pytest.fixture(autouse=True)
    def _setup(self, base_url):
        requests.post(
            _api(base_url, "/runs/register"),
            json={
                "gerrit_change": self.CHANGE,
                "gerrit_patchset": 1,
                "github_run_id": self.RUN_ID,
            },
            timeout=5,
        )
        _post_webhook(base_url, "workflow_run", {
            "action": "in_progress",
            "workflow_run": {
                "id": self.RUN_ID,
                "name": "CI",
                "status": "in_progress",
                "conclusion": None,
                "html_url": f"https://github.com/runs/{self.RUN_ID}",
                "run_number": 1,
                "run_attempt": 1,
                "display_title": "CI",
                "head_branch": f"refs/changes/{self.CHANGE % 100:02d}/{self.CHANGE}/1",
                "head_commit": None,
                "event": "repository_dispatch",
            },
        })

    def _send_job(self, base_url, status, conclusion=None):
        return _post_webhook(base_url, "workflow_job", {
            "action": status if status != "completed" else "completed",
            "workflow_job": {
                "id": self.JOB_ID,
                "run_id": self.RUN_ID,
                "name": "build-and-test",
                "status": status,
                "conclusion": conclusion,
                "html_url": f"https://github.com/jobs/{self.JOB_ID}",
                "started_at": "2025-01-01T00:00:00Z",
                "completed_at": "2025-01-01T00:10:00Z" if status == "completed" else None,
                "steps": [],
            },
        })

    def _get_job(self, base_url):
        runs = requests.get(
            _api(base_url, f"/changes/{self.CHANGE}/patchsets/1/runs"),
            timeout=5,
        ).json()["runs"]
        run = next(r for r in runs if r["github_run_id"] == self.RUN_ID)
        return next((j for j in run["jobs"] if j["github_job_id"] == self.JOB_ID), None)

    def test_job_transitions(self, base_url):
        # Queued
        self._send_job(base_url, "queued")
        job = self._get_job(base_url)
        assert job["status"] == "queued"

        # In progress
        self._send_job(base_url, "in_progress")
        job = self._get_job(base_url)
        assert job["status"] == "in_progress"

        # Completed with success
        self._send_job(base_url, "completed", "success")
        job = self._get_job(base_url)
        assert job["status"] == "completed"
        assert job["conclusion"] == "success"

    def test_job_failure(self, base_url):
        self._send_job(base_url, "completed", "failure")
        job = self._get_job(base_url)
        assert job["status"] == "completed"
        assert job["conclusion"] == "failure"

    def test_job_cancelled(self, base_url):
        self._send_job(base_url, "completed", "cancelled")
        job = self._get_job(base_url)
        assert job["conclusion"] == "cancelled"


# ---------------------------------------------------------------------------
# Gerrit webhook (queue) + queue status
# ---------------------------------------------------------------------------

class TestGerritWebhookQueue:
    """Test the Gerrit webhook endpoint that queues events for dispatch."""

    def _post_gerrit_event(self, base_url, change_number, owner="alice",
                           patchset=1, event_type="patchset-created"):
        return requests.post(
            _api(base_url, "/webhook/gerrit"),
            json={
                "type": event_type,
                "change": {
                    "number": change_number,
                    "subject": f"Test change {change_number}",
                    "url": f"https://review.spdk.io/c/spdk/spdk/+/{change_number}",
                    "owner": {"username": owner},
                    "status": "NEW",
                },
                "patchSet": {
                    "number": patchset,
                    "ref": f"refs/changes/{change_number % 100:02d}/{change_number}/{patchset}",
                },
            },
            timeout=5,
        )

    def test_gerrit_webhook_queues_event(self, base_url):
        r = self._post_gerrit_event(base_url, 6001)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "queued"
        assert data["change"] == 6001

    def test_gerrit_webhook_missing_change(self, base_url):
        r = requests.post(
            _api(base_url, "/webhook/gerrit"),
            json={"type": "patchset-created"},
            timeout=5,
        )
        assert r.status_code == 400

    def test_queue_status_returns_pending(self, base_url):
        self._post_gerrit_event(base_url, 6002, owner="alice")
        self._post_gerrit_event(base_url, 6003, owner="bob")

        r = requests.get(_api(base_url, "/queue/status"), timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert "pending" in data
        assert "in_progress" in data
        assert "max_workflows" in data
        assert "queue_interval_seconds" in data

        # Should have at least the 2 events we just queued
        pending_changes = {p["change_number"] for p in data["pending"]}
        assert 6002 in pending_changes
        assert 6003 in pending_changes

    def test_queue_status_empty(self, base_url):
        r = requests.get(_api(base_url, "/queue/status"), timeout=5)
        assert r.status_code == 200
        # May have events from other tests, but structure should be correct
        data = r.json()
        assert isinstance(data["pending"], list)
        assert isinstance(data["in_progress"], list)

    def test_wip_event_not_queued(self, base_url):
        r = requests.post(
            _api(base_url, "/webhook/gerrit"),
            json={
                "type": "patchset-created",
                "change": {
                    "number": 6004,
                    "subject": "WIP change",
                    "owner": {"username": "carol"},
                    "wip": True,
                    "status": "NEW",
                },
                "patchSet": {"number": 1, "ref": "refs/changes/04/6004/1"},
            },
            timeout=5,
        )
        assert r.status_code == 200

        status = requests.get(
            _api(base_url, "/queue/status"), timeout=5
        ).json()
        pending_changes = {p["change_number"] for p in status["pending"]}
        assert 6004 not in pending_changes
