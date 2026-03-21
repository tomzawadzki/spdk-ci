"""Tests for common.gerrit_helpers — Gerrit API helpers."""

from unittest.mock import patch, MagicMock

from common import gerrit_helpers


# ---------------------------------------------------------------------------
# get_current_revision
# ---------------------------------------------------------------------------

def test_get_current_revision_happy_path():
    change = {
        "current_revision": "abc123",
        "revisions": {
            "abc123": {"_number": 3, "ref": "refs/changes/45/12345/3"},
            "def456": {"_number": 2},
        },
    }
    result = gerrit_helpers.get_current_revision(change)
    assert result["_number"] == 3
    assert result["ref"] == "refs/changes/45/12345/3"


def test_get_current_revision_missing_revisions():
    assert gerrit_helpers.get_current_revision({}) == {}


def test_get_current_revision_no_current_revision_key():
    change = {"revisions": {"abc": {"_number": 1}}}
    assert gerrit_helpers.get_current_revision(change) == {}


def test_get_current_revision_bad_type():
    change = {"current_revision": "abc", "revisions": "not-a-dict"}
    assert gerrit_helpers.get_current_revision(change) == {}


# ---------------------------------------------------------------------------
# parse_gerrit_timestamp
# ---------------------------------------------------------------------------

def test_parse_gerrit_timestamp_valid():
    ts = gerrit_helpers.parse_gerrit_timestamp("2025-01-15 10:30:00.000000000")
    assert isinstance(ts, int)
    assert ts > 0


def test_parse_gerrit_timestamp_none():
    assert gerrit_helpers.parse_gerrit_timestamp(None) is None


def test_parse_gerrit_timestamp_empty():
    assert gerrit_helpers.parse_gerrit_timestamp("") is None


def test_parse_gerrit_timestamp_invalid():
    assert gerrit_helpers.parse_gerrit_timestamp("not-a-date") is None


# ---------------------------------------------------------------------------
# validate_change_for_ci
# ---------------------------------------------------------------------------

def _mock_gerrit_data(**overrides):
    """Return a valid Gerrit change dict, with optional overrides."""
    data = {
        "status": "NEW",
        "work_in_progress": False,
        "is_private": False,
        "revisions": {"abc123": {"_number": 3}},
        "labels": {},
    }
    data.update(overrides)
    return data


@patch("common.gerrit_helpers.get_gerrit_client")
def test_validate_change_for_ci_valid(mock_client):
    mock_client.return_value.get.return_value = _mock_gerrit_data()

    result = gerrit_helpers.validate_change_for_ci(
        "https://review.example.com", 12345, patchset_number=3)

    assert result["valid"] is True
    assert "data" in result


@patch("common.gerrit_helpers.get_gerrit_client")
def test_validate_change_for_ci_wip(mock_client):
    mock_client.return_value.get.return_value = _mock_gerrit_data(
        work_in_progress=True)

    result = gerrit_helpers.validate_change_for_ci(
        "https://review.example.com", 12345)

    assert result["valid"] is False
    assert "WIP" in result["error"]


@patch("common.gerrit_helpers.get_gerrit_client")
def test_validate_change_for_ci_private(mock_client):
    mock_client.return_value.get.return_value = _mock_gerrit_data(
        is_private=True)

    result = gerrit_helpers.validate_change_for_ci(
        "https://review.example.com", 12345)

    assert result["valid"] is False
    assert "private" in result["error"]


@patch("common.gerrit_helpers.get_gerrit_client")
def test_validate_change_for_ci_wrong_status(mock_client):
    mock_client.return_value.get.return_value = _mock_gerrit_data(
        status="MERGED")

    result = gerrit_helpers.validate_change_for_ci(
        "https://review.example.com", 12345)

    assert result["valid"] is False
    assert "MERGED" in result["error"]


@patch("common.gerrit_helpers.get_gerrit_client")
def test_validate_change_for_ci_stale_patchset(mock_client):
    mock_client.return_value.get.return_value = _mock_gerrit_data(
        revisions={"abc": {"_number": 5}})

    result = gerrit_helpers.validate_change_for_ci(
        "https://review.example.com", 12345, patchset_number=3)

    assert result["valid"] is False
    assert "not the latest" in result["error"]


@patch("common.gerrit_helpers.get_gerrit_client")
def test_validate_change_for_ci_gerrit_error(mock_client):
    mock_client.return_value.get.side_effect = Exception("timeout")

    result = gerrit_helpers.validate_change_for_ci(
        "https://review.example.com", 12345)

    assert result["valid"] is False
    assert "Failed to validate" in result["error"]


@patch("common.gerrit_helpers.get_gerrit_client")
def test_validate_change_for_ci_no_patchset_check(mock_client):
    """When patchset_number is None, skip the latest-patchset check."""
    mock_client.return_value.get.return_value = _mock_gerrit_data()

    result = gerrit_helpers.validate_change_for_ci(
        "https://review.example.com", 12345, patchset_number=None)

    assert result["valid"] is True
