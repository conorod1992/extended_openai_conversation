"""Stored canonical rules round-trip through the actual downloaded UTF-8 bytes."""

import json
from pathlib import Path
import subprocess

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    MAX_PACK_BYTES,
    export_rule_pack,
    validate_rule_pack,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from tests.test_request_rules import MemoryStore, local_rule


@pytest.mark.parametrize("unicode", [False, True])
async def test_verbose_valid_pack_is_importable_after_frontend_serialization(
    hass, unicode
):
    rules = []
    for index in range(310):
        rule = local_rule(
            f"Lights {index}",
            [f"activate lights {index}" + (" éclair 🏠" if unicode else "")],
        )
        rule["id"] = f"lights-{index}"
        rule["order"] = index
        rule["action"]["actions"] = [
            {
                "action": "light.turn_on",
                "target": {"entity_id": ["light.kitchen", "light.hall"]},
                "data": {"brightness_pct": 75, "transition": 2},
            }
            for _ in range(20)
        ]
        rules.append(rule)
    manager = RequestRules(MemoryStore({"rules": rules}))
    await manager.async_initialize()
    pack = export_rule_pack(manager, "all", reviewed=True)
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("rule-pack-download-wire.mjs"))],
        input=json.dumps(pack),
        capture_output=True,
        text=True,
        check=True,
    )
    wire = json.loads(result.stdout)
    assert wire["pretty_size"] > MAX_PACK_BYTES
    assert wire["size"] == len(wire["json"].encode("utf-8")) <= MAX_PACK_BYTES
    imported = validate_rule_pack(wire["json"])
    assert len(imported["rules"]) == 310
    assert all(len(rule["action"]["actions"]) == 20 for rule in imported["rules"])


def test_final_byte_guard_rejects_oversized_frontend_payload():
    pack = {"rules": [{"content": "🧠" * (MAX_PACK_BYTES // 4)}]}
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("rule-pack-download-wire.mjs"))],
        input=json.dumps(pack),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "2 MB" in json.loads(result.stdout)["error"]
