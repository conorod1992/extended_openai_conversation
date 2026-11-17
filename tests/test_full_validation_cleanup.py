"""Cancelled validation cleanup must leave any active cohort alone."""

from datetime import UTC, datetime

import pytest

from ci import full_validation_cleanup as cleanup

REPO = "owner/repo"
BRANCH = "full-validation-123-1"
SHA = "a" * 40
NOW = datetime(2026, 11, 17, 14, tzinfo=UTC)


@pytest.fixture
def cohort(monkeypatch):
    parent = {
        "id": 123,
        "path": ".github/workflows/full-validation.yml",
        "event": "workflow_dispatch",
        "head_repository": {"full_name": REPO},
        "head_sha": SHA,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "cancelled",
        "updated_at": "2026-11-17T12:00:00Z",
    }
    children = [
        {"id": 456, "head_branch": BRANCH, "status": "completed"},
        {"id": 789, "head_branch": BRANCH, "status": "completed"},
    ]
    pages = [{"total_count": 2, "workflow_runs": [child]} for child in children]

    def api(repository, path, *options):
        assert repository == REPO
        if path == "actions/runs/123":
            return parent
        assert path == f"actions/runs?branch={BRANCH}&per_page=100"
        assert options == ("--paginate", "--slurp")
        return pages

    monkeypatch.setattr(cleanup, "api", api)
    return parent, children, pages


def test_only_drained_cancelled_cohort_is_eligible(cohort):
    assert cleanup.eligible(REPO, f"refs/heads/{BRANCH}", SHA, NOW)


@pytest.mark.parametrize("status", ["queued", "waiting", "in_progress", "pending"])
def test_active_child_on_later_page_keeps_branch(cohort, status):
    _, children, _ = cohort
    children[-1]["status"] = status
    assert not cleanup.eligible(REPO, f"refs/heads/{BRANCH}", SHA, NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "in_progress"},
        {"conclusion": "success"},
        {"updated_at": "2026-11-17T13:59:00Z"},
        {"head_sha": "b" * 40},
        {"head_repository": {"full_name": "different/repo"}},
        {"path": ".github/workflows/other.yml"},
    ],
)
def test_parent_must_match_a_settled_cancellation(cohort, change):
    parent, _, _ = cohort
    parent.update(change)
    assert not cleanup.eligible(REPO, f"refs/heads/{BRANCH}", SHA, NOW)


def test_incomplete_listing_cannot_authorize_deletion(cohort):
    _, _, pages = cohort
    pages.pop()
    with pytest.raises(ValueError, match="Incomplete workflow listing"):
        cleanup.eligible(REPO, f"refs/heads/{BRANCH}", SHA, NOW)
