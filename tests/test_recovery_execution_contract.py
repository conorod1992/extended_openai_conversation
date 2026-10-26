"""Keep authored-draft and delayed recovery histories mandatory in certification."""

from copy import deepcopy
import json

import pytest

from ci.execution_contract import CONTRACT, check_execution, expected_cases

DRAFT_TITLE = (
    "deleted and recreated subentry retains the old draft until explicit discard"
)
DELAYED_PREFIX = (
    "tests_stress/test_delayed_mutation_matrix.py::"
    "test_delayed_tool_uses_live_state_at_due_time"
)


@pytest.mark.parametrize(
    "campaign,required",
    [
        (
            campaign,
            {
                "tests_browser/mounted-live-lifecycle-mutations.stress.mjs::"
                f"{engine}::mounted-live-lifecycle-mutations.stress.mjs::{DRAFT_TITLE}"
            },
        )
        for campaign, engine in (
            ("browser", "chromium"),
            ("browser-firefox", "firefox"),
            ("browser-webkit", "webkit"),
        )
    ]
    + [
        (
            "functions",
            {
                f"{DELAYED_PREFIX}[backup_restore_removed_tool]",
                f"{DELAYED_PREFIX}[backup_restore_legacy_defaults]",
            },
        )
    ],
)
def test_recovery_histories_require_actual_passes(campaign, required):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected = expected_cases(contract, campaign)
    assert required <= expected
    assert not (required & contract.get("allowances", {}).keys())
    evidence = {
        "campaign": campaign,
        "execution_cases": [
            {"nodeid": node, "collected": True, "executed": True, "outcome": "passed"}
            for node in sorted(expected)
        ],
        "measured_totals": contract.get("minimums", {}).get(campaign, {}),
    }
    assert check_execution(evidence, contract) == []
    for node in required:
        for outcome in (None, "failed", "skipped"):
            changed = deepcopy(evidence)
            if outcome is None:
                changed["execution_cases"] = [
                    case
                    for case in changed["execution_cases"]
                    if case["nodeid"] != node
                ]
            else:
                next(
                    case
                    for case in changed["execution_cases"]
                    if case["nodeid"] == node
                )["outcome"] = outcome
            assert any(node in error for error in check_execution(changed, contract))
