"""Effectiveness reports must not misclassify fixture or setup failures as kills."""
import pytest

from scripts.report_contract_effectiveness import REQUIRED, summarize


def _rows(outcomes):
    return {
        "sha": "test-sha",
        "results": [
            {"name": name, "baseline": baseline, "mutation": mutant}
            for name, (baseline, mutant) in outcomes.items()
        ],
    }


def test_all_critical_probes_must_be_independently_killed():
    results = summarize(_rows({name: ("survived", "killed") for name in REQUIRED}))
    assert results["required_pass"]
    assert results["required_regressions"] == []
    assert results["conclusive"] == len(REQUIRED)


@pytest.mark.parametrize("failure", [("survived", "survived"), ("invalid", "killed"), ("survived", "invalid")])
def test_critical_misses_or_invalid_runs_are_not_marked_green(failure):
    results = {name: ("survived", "killed") for name in REQUIRED}
    victim = sorted(REQUIRED)[0]
    results[victim] = failure
    report = summarize(_rows(results))
    assert not report["required_pass"]
    assert victim in report["required_regressions"]
    assert victim not in report["detected"]


def test_new_unreviewed_challenge_may_miss_without_downgrading_critical_baseline():
    results = {name: ("survived", "killed") for name in REQUIRED}
    results["unfamiliar-probe"] = ("survived", "survived")
    report = summarize(_rows(results))
    assert report["required_pass"]
    assert report["missed"] == ["unfamiliar-probe"]


def test_duplicate_names_rejected_and_missing_probes_fail_closed():
    cases = _rows({name: ("survived", "killed") for name in REQUIRED})
    cases["results"].append(dict(cases["results"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        summarize(cases)
    cases["results"].pop()
    cases["results"].pop()
    assert not summarize(cases)["required_pass"]
