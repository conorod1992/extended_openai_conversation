"""Scheduling and aggregate checks must fail closed without losing coverage."""

import json
from pathlib import Path

import pytest
import yaml

from ci.actions_schedule import (
    CAMPAIGNS,
    ENHANCED_DAILY,
    ENHANCED_WEEKLY,
    enhanced_plan,
)
from ci.pr_validation import POLICY, select, validation_errors


def test_daily_preserves_every_normal_feature_campaign():
    plan = enhanced_plan("schedule", ENHANCED_DAILY)
    assert plan == {"campaigns": list(CAMPAIGNS[:-1]), "intensities": ["normal"]}
    assert len(plan["campaigns"]) == 19
    assert {"guest-security", "persistence", "functions", "request-rules"} <= set(
        plan["campaigns"]
    )


def test_weekly_and_full_validation_preserve_full_programme():
    weekly = enhanced_plan("schedule", ENHANCED_WEEKLY)
    full = enhanced_plan("workflow_dispatch", campaign="all", intensity="all")
    assert (
        weekly
        == full
        == {"campaigns": list(CAMPAIGNS), "intensities": ["normal", "heavy"]}
    )
    assert enhanced_plan("workflow_dispatch", campaign="all", intensity="heavy")[
        "intensities"
    ] == ["heavy"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"event": "push"},
        {"event": "schedule", "schedule": "unknown"},
        {"event": "workflow_dispatch", "intensity": "unknown"},
    ],
)
def test_unknown_execution_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        enhanced_plan(**kwargs)


def test_documentation_and_backend_paths_select_distinct_coverage():
    docs = select(["docs/development/example.md"], "develop")
    assert docs["docs"] and not docs["real-ha"] and not docs["ci-image-stable"]
    backend = select(
        ["custom_components/extended_openai_conversation_responses/conversation.py"],
        "develop",
    )
    assert backend["ci"] and backend["real-ha"] and not backend["ci-image-stable"]
    assert select(["ci/Dockerfile.stable"], "develop")["ci-image-stable"]
    assert not select(["ci/pr_validation_unrelated.py"], "develop")["ci-image-stable"]


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["ci/pr_validation.py"],
        ["ci/pr_workflows.json"],
        [".github/workflows/pr-validation.yml"],
    ],
)
def test_selector_changes_force_every_lane(paths):
    assert all(select(paths, "develop").values())


def test_validation_requires_exact_dependency_results():
    plan = {"required": True, "excluded": False}
    needs = {
        "select": {"result": "success"},
        "required": {"result": "success"},
        "excluded": {"result": "skipped"},
    }
    assert not validation_errors(plan, needs, set(plan))
    for lane, result in [
        ("select", "failure"),
        ("required", "skipped"),
        ("required", "cancelled"),
        ("required", "failure"),
        ("excluded", "success"),
    ]:
        changed = {**needs, lane: {"result": result}}
        assert validation_errors(plan, changed, set(plan))
    assert validation_errors(plan, {"select": needs["select"]}, set(plan))
    assert validation_errors({"required": "true", "excluded": False}, needs, set(plan))


def test_aggregate_inventory_matches_reusable_lanes_and_needs():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    workflow = yaml.safe_load(
        Path(".github/workflows/pr-validation.yml").read_text(encoding="utf-8")
    )
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"pull_request": None}
    jobs = workflow["jobs"]
    assert set(jobs) == set(policy) | {"select", "validation"}
    assert set(jobs["validation"]["needs"]) == set(policy) | {"select"}
    assert jobs["validation"]["if"] in {"always()", "${{ always() }}"}
    assert jobs["validation"]["name"] == "PR validation"
    assert "git ls-remote" in jobs["validation"]["steps"][-1]["run"]
    for lane in policy:
        child = yaml.safe_load(Path(jobs[lane]["uses"]).read_text(encoding="utf-8"))
        child_triggers = child.get("on", child.get(True))
        assert "workflow_call" in child_triggers
        assert "pull_request" not in child_triggers
        assert "github.workflow" not in child.get("concurrency", {}).get("group", "")


def test_post_merge_smoke_resolves_required_environment_before_reconciliation():
    workflow = yaml.safe_load(Path(".github/workflows/post-merge-smoke.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["smoke"]["steps"]
    setup = next(step["run"] for step in steps if step.get("name") == "Reconcile stable environment")
    reconcile = setup.index("bash ci/reconcile_stable_environment.sh")
    for name in ("EOAI_EXPECTED_HA_VERSION", "EOAI_EXPECTED_HA_TEST_PLUGIN_VERSION"):
        assert setup.index("export " + name + "=") < reconcile
    assert "https://pypi.org/pypi/homeassistant/json" in setup
    assert 'ci/resolve_ha_test_plugin.py "$VERSION"' in setup
