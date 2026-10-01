"""Selected compatibility coverage cannot disappear or quietly skip."""

import json
from pathlib import Path

import pytest
import yaml

from ci.execution_contract import CONTRACT, check_execution, expected_cases

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "campaign",
    [
        "minimum-ha-features",
        "public-version-journeys",
        "native-browser-firefox",
        "native-browser-webkit",
        "native-browser-webkit-mobile",
    ],
)
def test_compatibility_requires_each_reviewed_case(campaign):
    policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
    nodes = expected_cases(policy, campaign)
    assert nodes
    item = {
        "campaign": campaign,
        "execution_cases": [
            {"nodeid": node, "collected": True, "executed": True, "outcome": "passed"}
            for node in sorted(nodes)
        ],
    }
    assert not check_execution(item, policy)
    for index in range(len(item["execution_cases"])):
        altered = json.loads(json.dumps(item))
        altered["execution_cases"][index].update(executed=False, outcome="skipped")
        assert check_execution(altered, policy)
        altered["execution_cases"].pop(index)
        assert check_execution(altered, policy)


def test_only_stable_adds_native_browser_engines():
    jobs = yaml.safe_load(
        (ROOT / ".github/workflows/ha-browser-compatibility.yml").read_text(
            encoding="utf-8"
        )
    )["jobs"]
    matrix = jobs["native-browser"]["strategy"]["matrix"]
    assert matrix["ha-version"] == ["oldest", "stable", "dev"]
    assert matrix["profile"] == ["chromium"]
    assert matrix["include"] == [
        {"ha-version": "stable", "profile": p}
        for p in ["firefox", "webkit", "webkit-mobile"]
    ]
    steps = jobs["native-browser"]["steps"]
    assert any(
        "compatibility_evidence.py check" in step.get("run", "") for step in steps
    )


def test_minimum_feature_contract_covers_distinct_native_boundaries():
    policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
    nodes = expected_cases(policy, "minimum-ha-features")
    for file in [
        "test_public_version_journeys",
        "test_ha_llm_tool_acceptance",
        "test_native_indirect_target_resolution",
        "test_user_permission_acceptance",
        "test_local_intent_exclusions",
        "test_assist_voice_identity_precedence",
        "test_request_rules_script_semantics",
        "test_entity_registry_customization",
        "test_knowledge_provider_wire_e2e",
        "test_memory_provider_wire_e2e",
        "test_quiet_hours_acceptance",
        "test_intercom_voice_acceptance",
    ]:
        assert any(node.startswith(f"tests_real_ha/{file}.py::") for node in nodes), (
            file
        )
