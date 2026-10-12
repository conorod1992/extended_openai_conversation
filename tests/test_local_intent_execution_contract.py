"""Keep the real HA permission cases mandatory in compatibility evidence."""

from copy import deepcopy
import json

import pytest

from ci.execution_contract import CONTRACT, check_execution, expected_cases


@pytest.mark.parametrize("campaign", ["minimum-ha-features", "feature-crossroads"])
def test_local_state_permission_cases_are_required(campaign):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    cases = {
        "tests_real_ha/test_local_intent_exclusions.py::"
        f"test_local_state_query_respects_real_ha_read_permissions[{caller}]"
        for caller in ("owner", "restricted", "voice")
    }
    expected = expected_cases(contract, campaign)
    assert cases <= expected
    evidence = {
        "campaign": campaign,
        "execution_cases": [
            {"nodeid": node, "collected": True, "executed": True, "outcome": "passed"}
            for node in sorted(expected)
        ],
        "measured_totals": contract.get("minimums", {}).get(campaign, {}),
    }
    assert check_execution(evidence, contract) == []
    for node in cases:
        missing = deepcopy(evidence)
        missing["execution_cases"] = [
            case for case in missing["execution_cases"] if case["nodeid"] != node
        ]
        assert f"{node}: mandatory case absent" in check_execution(missing, contract)
        failed = deepcopy(evidence)
        next(case for case in failed["execution_cases"] if case["nodeid"] == node)[
            "outcome"
        ] = "failed"
        assert any(node in error for error in check_execution(failed, contract))
