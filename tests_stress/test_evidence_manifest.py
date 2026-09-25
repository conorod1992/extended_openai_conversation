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
