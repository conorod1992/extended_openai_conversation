"""New wording edits reject ambiguity; saved catalogs isolate it losslessly."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    async_request_rule_match_preview,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRules,
    normalize_text,
    validate_wording_groups,
)
from tests.test_request_rule_matching_contracts import MemoryStore, rule

COLLIDING = [
    {"canonical": "light", "alternatives": ["lamp"]},
    {"canonical": "device", "alternatives": ["lamps"]},
    {"canonical": "television", "alternatives": ["telly"]},
]


def test_new_catalogs_reject_real_collisions_but_allow_same_canonical_aliases():
    with pytest.raises(ValueError, match="ambiguous"):
        validate_wording_groups(COLLIDING)
    benign = validate_wording_groups(
        [
            {"canonical": "light", "alternatives": ["lamp", "light"]},
            {"canonical": "lights", "alternatives": ["bulbs"]},
        ]
    )
    for term in ("lamp", "lamps", "bulbs", "light", "lights"):
        assert normalize_text(term, DEFAULT_MATCHING, benign) == "light"


@pytest.mark.parametrize("forms", [False, True])
async def test_saved_collision_preserves_unrelated_matches_and_preview(
    hass, forms, caplog
):
    data = {
        "rules": [rule("television", "television")],
        "wording_groups": deepcopy(COLLIDING),
        "defaults": {**DEFAULT_MATCHING, "word_forms": forms},
    }
    store = MemoryStore(data)
    manager = RequestRules(store)
    await manager.async_initialize()
    assert manager.match("telly").rule["id"] == "television"
    assert (await async_request_rule_match_preview(hass, manager, "telly"))["matched"]
    assert manager.snapshot()["wording_groups"] == COLLIDING
    assert manager.snapshot()["wording_diagnostics"] == {"word_forms": 1}
    assert "unrelated mappings retained" in caplog.text
    saved = await manager.async_backup_data()
    assert saved["wording_groups"] == COLLIDING
    assert "wording_diagnostics" not in saved
    restored = RequestRules(MemoryStore(RequestRules.validate_backup_data(saved)))
    await restored.async_initialize()
    assert restored.match("telly") is not None
    assert (
        normalize_text("lamp", {**DEFAULT_MATCHING, "word_forms": True}, COLLIDING)
        == "lamp"
    )
    assert (
        normalize_text("lamps", {**DEFAULT_MATCHING, "word_forms": False}, COLLIDING)
        == "device"
    )
    before = manager.snapshot()
    with pytest.raises(ValueError, match="ambiguous"):
        await manager.async_set_wording_groups(COLLIDING)
    assert manager.snapshot() == before


async def test_legacy_basic_collision_is_isolated_without_discarding_catalog():
    groups = [
        {"canonical": "light", "alternatives": ["lamp"]},
        {"canonical": "device", "alternatives": ["lamp"]},
        COLLIDING[-1],
    ]
    manager = RequestRules(
        MemoryStore({"rules": [rule("tv", "television")], "wording_groups": groups})
    )
    await manager.async_initialize()
    assert manager.match("telly") is not None
    assert manager.snapshot()["wording_groups"] == groups
    assert manager.snapshot()["wording_diagnostics"] == {"basic": 1, "word_forms": 1}
