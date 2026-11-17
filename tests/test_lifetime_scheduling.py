"""Scheduling hints cannot replace completed, candidate-bound lifetime results."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ci import lifetime_scheduling as scheduling

SHA = "a" * 40
NAMES = {"long-lifetime / heavy / reload", "long-lifetime / heavy / uninterrupted"}


def job(name, **changes):
    return {
        "name": name,
        "head_sha": SHA,
        "run_attempt": 2,
        "status": "in_progress",
        **changes,
    }


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=0, sleeps=[])

    def sleep(seconds):
        state.now += seconds
        state.sleeps.append(seconds)

    monkeypatch.setattr(
        scheduling, "time", SimpleNamespace(monotonic=lambda: state.now, sleep=sleep)
    )
    return state


def test_fanout_waits_for_both_lifetime_jobs_but_not_their_results(clock):
    reload, uninterrupted = sorted(NAMES)
    get = Mock(
        side_effect=[
            {
                "jobs": [
                    job(reload),
                    job(
                        uninterrupted,
                        status="queued",
                        started_at="already set by GitHub",
                    ),
                ]
            },
            {
                "jobs": [
                    job(reload, status="completed", conclusion="failure"),
                    job(uninterrupted),
                ]
            },
        ]
    )
    assert scheduling.wait_for_lifetime_start(get, 123, 2, SHA, NAMES)
    assert clock.sleeps == [5]
    assert get.call_args.args == (
        "actions/runs/123/attempts/2/jobs?per_page=100&page=1",
    )


@pytest.mark.parametrize(
    "damage",
    [
        {"head_sha": "b" * 40},
        {"run_attempt": 1},
        {"status": "queued"},
        {"status": "waiting"},
    ],
)
def test_wrong_candidate_retry_or_unstarted_job_cannot_release_early(clock, damage):
    get = Mock(return_value={"jobs": [job(name, **damage) for name in NAMES]})
    assert not scheduling.wait_for_lifetime_start(get, 123, 2, SHA, NAMES, timeout=12)
    assert clock.now == 12
    assert clock.sleeps == [5, 5, 2]


def test_job_pagination_does_not_hide_the_second_lifetime_partition(clock):
    first, second = sorted(NAMES)
    get = Mock(
        side_effect=[
            {"jobs": [job(first), *[job(f"short-{i}") for i in range(99)]]},
            {"jobs": [job(second)]},
        ]
    )
    assert scheduling.wait_for_lifetime_start(get, 123, 2, SHA, NAMES)
    assert get.call_args.args[0].endswith("page=2")
    assert not clock.sleeps


def test_unavailable_status_releases_remaining_tests_without_acceptance_credit(
    clock, capsys
):
    get = Mock(side_effect=RuntimeError("status API unavailable"))
    assert not scheduling.wait_for_lifetime_start(get, 123, 2, SHA, NAMES)
    assert "::warning::" in capsys.readouterr().out
    assert not clock.sleeps


def test_no_lifetime_selection_does_not_read_status_or_delay_quick_work(clock):
    get = Mock()
    assert scheduling.wait_for_lifetime_start(get, 123, 2, SHA, set())
    get.assert_not_called()
    assert not clock.sleeps


def test_selected_intensities_and_partitions_keep_the_existing_certification_names():
    rows = [
        {"campaign": "long-lifetime", "intensity": intensity, "partition": part}
        for intensity in ("normal", "heavy")
        for part in ("reload", "uninterrupted")
    ]
    assert scheduling.lifetime_job_names(rows) == NAMES | {
        "long-lifetime / normal / reload",
        "long-lifetime / normal / uninterrupted",
    }
