"""Require explicit evidence classification when supported behavior grows."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.extended_openai_conversation_responses.functions import FUNCTIONS
from custom_components.extended_openai_conversation_responses.request_rules import (
    ACTION_TYPES,
)
from tests_stress.test_backup_inventory import BACKED_UP_SUBSYSTEMS

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests_stress" / "evidence_manifest.json"
BASE_FEATURES = {
    "initial_setup",
    "ai_task",
    "voice_identity",
    "intercom",
    "conversation_archive",
    "skills",
    "local_intents",
    "streamed_speech",
    "usage_retention",
    "model_catalog",
    "entity_exposure",
    "reauth",
    "guest_mode",
    "quiet_hours",
    "function_groups",
    "delayed_functions",
    "backup",
    "backup_transfer",
    "historical_restore",
    "large_installation",
    "browser_reconnect",
    "multi_tab_conflict",
    "immediate_function_crash",
}
LAYERS = {"model", "real_ha", "provider_wire", "browser", "browser_real_ha", "process"}
GENUINE_HA_BROWSER_SPECS = {
    "tests_browser/real-ha-shell.spec.mjs",
    "tests_browser/real-ha-backend.spec.mjs",
    "tests_browser/real-ha-multi-tab.stress.mjs",
}


def test_supported_features_have_reviewed_evidence_layer_entries() -> None:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert set(document["layers"]) == LAYERS
    features = document["features"]
    expected = (
        BASE_FEATURES
        | {f"function:{name}" for name in FUNCTIONS}
        | {f"request_rule_action:{name}" for name in ACTION_TYPES}
        | {f"store:{name}" for name in BACKED_UP_SUBSYSTEMS}
    )
    assert set(features) == expected, (
        "Review evidence layers when adding/removing a Function type, Request Rule "
        "action, backed-up subsystem, or major supported feature."
    )
    for feature, classified in features.items():
        assert classified, feature
        assert set(classified) <= LAYERS, feature
        for layer, references in classified.items():
            assert references, (feature, layer)
            assert len(references) == len(set(references)), (feature, layer)
            for reference in references:
                if layer == "browser_real_ha":
                    assert reference in GENUINE_HA_BROWSER_SPECS, (feature, reference)
                path = Path(reference)
                assert not path.is_absolute() and ".." not in path.parts
                assert path.parts[0] in {
                    "tests",
                    "tests_real_ha",
                    "tests_stress",
                    "tests_browser",
                }
                assert (ROOT / path).is_file(), (feature, layer, reference)


def _behavior_test(reference: str) -> None:
    """Resolve named assertions, not a file that merely happens to exist."""
    import ast

    path, separator, node = reference.partition("::")
    assert separator and path.startswith(("tests/", "tests_real_ha/", "tests_stress/")), reference
    assert ".." not in Path(path).parts, reference
    assert "inventory" not in Path(path).stem and "manifest" not in Path(path).stem, reference
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    matches = [item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == node]
    assert len(matches) == 1, reference
    assert any(isinstance(item, ast.Assert) for item in ast.walk(matches[0])), reference


def test_configuration_controls_require_semantic_outcomes() -> None:
    """New form controls cannot inherit persistence-only coverage silently."""
    interactions = json.loads((ROOT / "tests_stress/frontend_interaction_inventory.json").read_text())
    actions = json.loads((ROOT / "tests_stress/management_action_inventory.json").read_text())["sections"]
    expected = {
        f"{route}::{item['id']}": item
        for route, data in interactions["routes"].items()
        for item in data["interactions"]
        if item["kind"] in {"form", "dynamic_form", "form_action", "toggle", "collection", "runtime_journey"} and item["backend_actions"]
    }
    classified = json.loads(MANIFEST.read_text())["configuration_semantics"]
    assert set(classified) == set(expected), "Review runtime effects for every new configuration/control interaction"
    for key, contract in classified.items():
        interaction = expected[key]
        assert contract["controls"] == interaction["source_markers"], f"{key}: controls changed; review semantic evidence"
        kind = contract["classification"]
        if kind == "presentation_only":
            assert key in {"assistant/basics::agent_title_save_revert", "capabilities/request-rules::group_management"} and contract["reason"].strip()
        elif kind == "read_only_filter":
            assert contract["reason"].strip()
            assert all(actions[section]["semantic_classes"][action] == "read_only" for section, action in (value.split("/") for value in interaction["backend_actions"]))
        else:
            assert kind == "runtime_effect" and contract["effect"].strip(), key
            assert contract["evidence"], key
            for reference in contract["evidence"]:
                _behavior_test(reference)


def test_governance_cannot_cite_itself_as_semantic_evidence() -> None:
    import pytest

    with pytest.raises(AssertionError):
        _behavior_test("tests_stress/test_evidence_manifest.py::test_supported_features_have_reviewed_evidence_layer_entries")


def test_fresh_debug_export_credentials_redacted_and_intended_user_content_retained(
    tmp_path, stress_seed, stress_trace
):
    """Inspect an actual DebugTrace export under its credential-only privacy policy."""
    from ci.enhanced_evidence import fresh_privacy_canaries
    from tests.test_request_diagnostics import _trace
    from tests_stress.conftest import record

    probes = fresh_privacy_canaries(str(stress_seed))
    trace = _trace()
    trace.start_provider_request(
        "responses",
        (),
        {
            "input": [
                {
                    "role": "user",
                    "content": "INTENDED-DEBUG-USER-CONTENT",
                    "metadata": {"api_key": probes[0]},
                }
            ],
            "headers": {"Authorization": probes[1]},
            "tools": [],
        },
    )
    export = tmp_path / "actual-debug-export.json"
    export.write_text(json.dumps(trace.as_dict()), encoding="utf-8")
    uploaded = export.read_text(encoding="utf-8")
    assert probes[0] not in uploaded and probes[1] not in uploaded
    assert "INTENDED-DEBUG-USER-CONTENT" in uploaded
    assert "redacted credential" in uploaded
    record(stress_trace, "summary", layer="debug export", debug_export_privacy_cases=1)
