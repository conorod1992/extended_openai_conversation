"""Authenticated Management transport preserves typed Functions and empty edits."""

from copy import deepcopy
from datetime import date

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    management_function_repair,
)
from custom_components.extended_openai_conversation_responses.function_yaml import (
    decode_management_function_values,
)
from tests_real_ha.test_config_flow import (
    _make_entry,
    _setup_entry,
    _subentry_id,
    _unload_entry,
)
from tests_real_ha.test_management_command_contracts import _client, _raw_call


@pytest.mark.parametrize(
    "value", [date(2026, 10, 17), b"Hello\x00\xff"], ids=["date", "binary"]
)
async def test_typed_function_websocket_editor_and_draft_roundtrip(
    hass, hass_ws_client, value
):
    entry = _make_entry(conversation_data={"functions": "[]"})
    await _setup_entry(hass, entry)
    try:
        subentry_id = _subentry_id(entry, "conversation")
        client = await _client(
            hass, hass_ws_client, user_id="typed-yaml-admin", owner=True
        )

        async def call(section, action, **payload):
            response = await _raw_call(
                client,
                entry_id=entry.entry_id,
                subentry_id=subentry_id,
                section=section,
                action=action,
                **payload,
            )
            assert response["success"], response
            return response["result"]

        current = await call("configuration", "get")
        tool = {
            "spec": {
                "name": "typed_tool",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {
                "type": "script",
                "variables": {"marker": value},
                "sequence": [{"stop": "Done"}],
            },
        }
        saved = await call(
            "tools", "save", yaml=yaml.safe_dump(tool), revision=current["revision"]
        )
        assert decode_management_function_values(saved)["functions"][0] == tool
        subentry = entry.subentries[subentry_id]
        before = deepcopy(subentry.data)
        editor = await call(
            "tools", "serialize", name="typed_tool", revision=saved["revision"]
        )
        assert yaml.safe_load(editor["yaml"]) == tool
        assert subentry.data == before
        unchanged = await call(
            "tools",
            "save",
            yaml=editor["yaml"],
            original_name="typed_tool",
            revision=saved["revision"],
        )
        # The frontend may submit an explicit structured draft instead of YAML.
        unchanged["functions"][0]["enabled"] = False
        updated = await call(
            "configuration",
            "save",
            config={"functions": unchanged["functions"]},
            revision=unchanged["revision"],
        )
        await hass.async_block_till_done()
        subentry = entry.subentries[subentry_id]
        stored = yaml.safe_load(subentry.data["functions"])[0]
        assert stored["function"] == tool["function"]
        assert stored["enabled"] is False
        for cold in (False, True):
            if cold:
                management_function_repair.discard_persisted_config_projection(subentry)
            read = await call("configuration", "get")
            assert decode_management_function_values(read)["config"]["functions"] == [
                stored
            ]
        stale = await _raw_call(
            client,
            entry_id=entry.entry_id,
            subentry_id=subentry_id,
            section="tools",
            action="serialize",
            name="typed_tool",
            revision=saved["revision"],
        )
        assert (
            not stale["success"]
            and "Configuration changed" in stale["error"]["message"]
        )
        assert updated["revision"] != saved["revision"]
    finally:
        await _unload_entry(hass, entry)


@pytest.mark.parametrize("empty", [None, ""], ids=["json-null", "blank-yaml"])
async def test_explicit_empty_edit_clears_legacy_default_functions(
    hass, hass_ws_client, empty
):
    entry = _make_entry(conversation_data={"functions": empty})
    await _setup_entry(hass, entry)
    try:
        subentry_id = _subentry_id(entry, "conversation")
        client = await _client(
            hass, hass_ws_client, user_id="empty-functions-admin", owner=True
        )
        before = await _raw_call(
            client,
            entry_id=entry.entry_id,
            subentry_id=subentry_id,
            section="configuration",
            action="get",
        )
        assert before["success"] and len(before["result"]["config"]["functions"]) == 4
        saved = await _raw_call(
            client,
            entry_id=entry.entry_id,
            subentry_id=subentry_id,
            section="configuration",
            action="save",
            config={"functions": empty},
            revision=before["result"]["revision"],
        )
        assert saved["success"], saved
        assert saved["result"]["config"]["functions"] == []
        await hass.async_block_till_done()
        assert (
            agent_config.parse_configured_function_tools(
                entry.subentries[subentry_id].data
            )
            == []
        )
    finally:
        await _unload_entry(hass, entry)
