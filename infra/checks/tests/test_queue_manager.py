"""Tests for queue_manager — fair scheduling, filtering, enqueue/status."""

import sys
import os

CHECKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFRA_DIR = os.path.dirname(CHECKS_DIR)
if CHECKS_DIR not in sys.path:
    sys.path.insert(0, CHECKS_DIR)
if INFRA_DIR not in sys.path:
    sys.path.insert(0, INFRA_DIR)

from collections import deque
from unittest.mock import patch, MagicMock

import pytest

import queue_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(change_number, owner="alice", patchset=1, subject="test",
                wip=False, private=False, status="NEW", is_open=True):
    return {
        "type": "patchset-created",
        "change_number": change_number,
        "payload": {
            "type": "patchset-created",
            "change": {
                "number": change_number,
                "subject": subject,
                "url": f"https://review.spdk.io/c/spdk/spdk/+/{change_number}",
                "owner": {"username": owner},
                "wip": wip,
                "private": private,
                "open": is_open,
                "status": status,
            },
            "patchSet": {
                "number": patchset,
                "ref": f"refs/changes/{change_number % 100:02d}/{change_number}/{patchset}",
            },
        },
    }


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset queue state between tests."""
    with queue_manager._lock:
        queue_manager._pending_events.clear()
        queue_manager._dispatched_owners.clear()
    yield
    with queue_manager._lock:
        queue_manager._pending_events.clear()
        queue_manager._dispatched_owners.clear()


# ---------------------------------------------------------------------------
# Event filtering
# ---------------------------------------------------------------------------

class TestShouldDropEvent:
    def test_normal_event_not_dropped(self):
        drop, reason = queue_manager._should_drop_event(_make_event(1))
        assert not drop
        assert reason is None

    def test_wip_dropped(self):
        drop, reason = queue_manager._should_drop_event(_make_event(1, wip=True))
        assert drop
        assert reason == "wip"

    def test_private_dropped(self):
        drop, reason = queue_manager._should_drop_event(_make_event(1, private=True))
        assert drop
        assert reason == "private"

    def test_closed_dropped(self):
        drop, reason = queue_manager._should_drop_event(
            _make_event(1, is_open=False))
        assert drop
        assert reason == "closed"

    def test_merged_dropped(self):
        drop, reason = queue_manager._should_drop_event(
            _make_event(1, status="MERGED"))
        assert drop
        assert "MERGED" in reason


# ---------------------------------------------------------------------------
# Fair scheduling
# ---------------------------------------------------------------------------

class TestSelectFairEvent:
    def test_new_owner_gets_priority(self):
        pending = {
            1: _make_event(1, owner="alice"),
            2: _make_event(2, owner="bob"),
        }
        dispatched = deque(["alice"])
        selected = queue_manager._select_fair_event(pending, dispatched)
        assert selected == 2  # bob is new, gets priority

    def test_round_robin_when_all_dispatched(self):
        pending = {
            1: _make_event(1, owner="alice"),
            2: _make_event(2, owner="bob"),
        }
        # alice dispatched first, then bob
        dispatched = deque(["alice", "bob"])
        selected = queue_manager._select_fair_event(pending, dispatched)
        assert selected == 1  # alice is at front (oldest), goes next

    def test_single_event(self):
        pending = {42: _make_event(42, owner="charlie")}
        dispatched = deque()
        selected = queue_manager._select_fair_event(pending, dispatched)
        assert selected == 42

    def test_none_owner_treated_as_new(self):
        ev = _make_event(1, owner="alice")
        ev["payload"]["change"]["owner"] = {}  # no username
        pending = {1: ev, 2: _make_event(2, owner="bob")}
        dispatched = deque(["bob"])
        selected = queue_manager._select_fair_event(pending, dispatched)
        assert selected == 1  # None owner not in dispatched → priority


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:
    def test_enqueue_adds_event(self):
        queue_manager.enqueue(_make_event(100))
        with queue_manager._lock:
            assert 100 in queue_manager._pending_events

    def test_enqueue_replaces_duplicate(self):
        queue_manager.enqueue(_make_event(100, patchset=1))
        queue_manager.enqueue(_make_event(100, patchset=2))
        with queue_manager._lock:
            assert len(queue_manager._pending_events) == 1
            ps = queue_manager._pending_events[100]["payload"]["patchSet"]["number"]
            assert ps == 2

    def test_enqueue_drops_wip(self):
        queue_manager.enqueue(_make_event(100, wip=True))
        with queue_manager._lock:
            assert 100 not in queue_manager._pending_events

    def test_enqueue_drops_private(self):
        queue_manager.enqueue(_make_event(100, private=True))
        with queue_manager._lock:
            assert 100 not in queue_manager._pending_events

    def test_enqueue_no_change_number(self):
        queue_manager.enqueue({"type": "unknown", "payload": {}})
        with queue_manager._lock:
            assert len(queue_manager._pending_events) == 0


# ---------------------------------------------------------------------------
# Get status
# ---------------------------------------------------------------------------

class TestGetStatus:
    @patch("queue_manager._get_active_workflow_runs", return_value=[])
    def test_empty_status(self, _mock):
        status = queue_manager.get_status()
        assert status["pending"] == []
        assert status["in_progress"] == []

    @patch("queue_manager._get_active_workflow_runs", return_value=[])
    def test_pending_shows_fair_order(self, _mock):
        queue_manager.enqueue(_make_event(1, owner="alice"))
        queue_manager.enqueue(_make_event(2, owner="bob"))
        queue_manager.enqueue(_make_event(3, owner="alice"))

        status = queue_manager.get_status()
        changes = [p["change_number"] for p in status["pending"]]
        # alice(1) first (new owner), bob(2) next (new owner), alice(3) last
        assert changes == [1, 2, 3]

    @patch("queue_manager._get_active_workflow_runs")
    def test_in_progress_parsed_from_display_title(self, mock_runs):
        mock_runs.return_value = [
            {"display_title": "(12345/5) Fix bug", "html_url": "https://github.com/runs/1"},
        ]
        status = queue_manager.get_status()
        assert len(status["in_progress"]) == 1
        assert status["in_progress"][0]["change_number"] == 12345
        assert status["in_progress"][0]["patchset_number"] == 5
        assert status["in_progress"][0]["subject"] == "Fix bug"


# ---------------------------------------------------------------------------
# Process once
# ---------------------------------------------------------------------------

class TestProcessOnce:
    @patch("queue_manager._dispatch_event", return_value=True)
    @patch("queue_manager._get_active_workflow_runs", return_value=[])
    def test_dispatches_pending_events(self, _mock_runs, mock_dispatch):
        queue_manager.enqueue(_make_event(1, owner="alice"))
        queue_manager.enqueue(_make_event(2, owner="bob"))

        queue_manager._process_once()

        assert mock_dispatch.call_count == 2
        with queue_manager._lock:
            assert len(queue_manager._pending_events) == 0

    @patch("queue_manager._dispatch_event", return_value=True)
    @patch("queue_manager._get_active_workflow_runs")
    def test_respects_max_workflows(self, mock_runs, mock_dispatch, monkeypatch):
        monkeypatch.setattr("queue_manager.config.max_running_workflows", 2)
        # 1 already running
        mock_runs.return_value = [
            {"display_title": "(999/1) Running", "html_url": ""},
        ]
        queue_manager.enqueue(_make_event(1, owner="alice"))
        queue_manager.enqueue(_make_event(2, owner="bob"))

        queue_manager._process_once()

        # Only 1 dispatched (max 2 - 1 running = 1 slot)
        assert mock_dispatch.call_count == 1
        with queue_manager._lock:
            assert len(queue_manager._pending_events) == 1

    @patch("queue_manager._dispatch_event", return_value=False)
    @patch("queue_manager._get_active_workflow_runs", return_value=[])
    def test_stops_on_dispatch_failure(self, _mock_runs, mock_dispatch):
        queue_manager.enqueue(_make_event(1))
        queue_manager.enqueue(_make_event(2))

        queue_manager._process_once()

        # First dispatch fails → stops, doesn't try second
        assert mock_dispatch.call_count == 1
        with queue_manager._lock:
            assert len(queue_manager._pending_events) == 2

    @patch("queue_manager._get_active_workflow_runs", return_value=[])
    def test_clears_dispatched_owners_when_empty(self, _mock):
        with queue_manager._lock:
            queue_manager._dispatched_owners.extend(["alice", "bob"])

        queue_manager._process_once()  # no pending events

        with queue_manager._lock:
            assert len(queue_manager._dispatched_owners) == 0

    @patch("queue_manager._dispatch_event", return_value=True)
    @patch("queue_manager._get_active_workflow_runs", return_value=[])
    def test_fair_dispatch_order(self, _mock_runs, mock_dispatch):
        """Verify owner-based round-robin in dispatch order."""
        queue_manager.enqueue(_make_event(1, owner="alice"))
        queue_manager.enqueue(_make_event(2, owner="bob"))
        queue_manager.enqueue(_make_event(3, owner="alice"))

        queue_manager._process_once()

        # All 3 dispatched. Order: alice(1), bob(2), alice(3)
        calls = mock_dispatch.call_args_list
        dispatched = [c[0][0]["change_number"] for c in calls]
        assert dispatched == [1, 2, 3]
