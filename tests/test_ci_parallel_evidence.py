"""Parallel reads retain ordering, bounded concurrency and rejected evidence."""

from threading import Barrier, Event, Lock

import pytest

from ci import nightly_programme
from ci.resolve_ha_test_plugin import GENERATED_FIXTURES, resolve_ha_test_plugin
from tests.test_release_certification import SOURCE, FakeActions


@pytest.mark.parametrize("failure", [None, "newer", "older"])
def test_parallel_metadata_consumes_ranked_results_not_completion_order(failure):
    older_finished = Event()
    project = {"releases": {v: [{}] for v in ("0.13.375", "0.13.374")}}

    def read(url):
        newer = "/0.13.375/" in url
        if newer:
            assert older_finished.wait(5)
        else:
            older_finished.set()
        if failure == ("newer" if newer else "older"):
            raise RuntimeError("metadata unavailable")
        return {"info": {"requires_dist": ["homeassistant==2026.9.4"]}}

    if failure == "newer":
        with pytest.raises(RuntimeError, match="metadata unavailable"):
            resolve_ha_test_plugin("2026.9.4", project, read)
    else:
        # An older speculative error cannot overrule an already-matching newer
        # release. Conversely, a newer error cannot be bypassed by an older hit.
        assert resolve_ha_test_plugin("2026.9.4", project, read) == "0.13.375"


def test_metadata_parallelism_is_bounded_and_search_reaches_reviewed_fallback():
    barrier, lock = Barrier(8), Lock()
    active = peak = 0
    requested = []
    project = {"releases": {f"0.13.{n}": [{}] for n in range(16)}}

    def read(url):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            requested.append(url)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        return {"info": {"requires_dist": ["homeassistant==2026.10.0b4"]}}

    fixture = GENERATED_FIXTURES["2026.11.2"]
    assert resolve_ha_test_plugin("2026.11.2", project, read) == fixture.plugin_version
    assert peak == 8
    assert len(set(requested)) == 16


def test_metadata_search_does_not_prefetch_unbounded_older_batches():
    requested, lock = [], Lock()
    project = {"releases": {f"0.13.{n}": [{}] for n in range(16)}}

    def read(url):
        with lock:
            requested.append(url.rsplit("/", 2)[-2])
        return {"info": {"requires_dist": ["homeassistant==2026.9.4"]}}

    assert resolve_ha_test_plugin("2026.9.4", project, read) == "0.13.15"
    assert set(requested) == {f"0.13.{n}" for n in range(8, 16)}


@pytest.mark.parametrize("damaged", [False, True])
def test_parallel_programme_collection_retains_checks_and_inventory_order(
    monkeypatch, damaged
):
    workflows = (
        "official-ha-container.yml", "mutation.yml", "ci.yml", "frontend.yml"
    )
    barrier = Barrier(4)

    class ConcurrentActions(FakeActions):
        def workflow_runs(self, workflow, sha):
            barrier.wait(timeout=5)
            return super().workflow_runs(workflow, sha)

    actions = ConcurrentActions()
    if damaged:
        run_id = actions.runs["official-ha-container.yml"][0]["id"]
        actions.artifacts[run_id][0]["expired"] = True
    monkeypatch.setattr(nightly_programme, "WORKFLOWS", workflows)
    if damaged:
        with pytest.raises(RuntimeError, match="Missing official HA Container"):
            nightly_programme.collect(actions, SOURCE)
    else:
        evidence = nightly_programme.collect(actions, SOURCE)
        assert tuple(evidence) == workflows
        assert not nightly_programme.programme_errors(
            SOURCE, "2026.9.4", workflows, evidence, full_architecture=True
        )
