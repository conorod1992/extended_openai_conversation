"""Scoped writes through genuine HA WebSockets, services and subentry flows."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.guest_mode import (
    resolve_guest_policy,
)
from custom_components.extended_openai_conversation_responses.helpers import (
    get_exposed_entities,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    render_effective_prompt,
)
from custom_components.extended_openai_conversation_responses.services import (
    SERVICE_DISABLE_FUNCTION_TOOLS,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.data_entry_flow import FlowResultType
from tests.test_scoped_configuration_persistence import legacy_config
from tests_real_ha.test_config_flow import (
    _make_entry,
    _settle_entry,
    _setup_entry,
    _subentry_id,
    _unload_entry,
)
from tests_real_ha.test_management_command_contracts import _client, _raw_call


async def test_legacy_retention_websocket_and_function_service_preserve_policy(
    hass, hass_ws_client
):
    raw = legacy_config()
    raw["functions"] = [
        {
            "spec": {
                "name": "probe",
                "description": "Probe",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "get_user_from_user_id"},
            "enabled": True,
        }
    ]
    entry = _make_entry(conversation_data=raw)
    await _setup_entry(hass, entry)
    try:
        subentry_id = _subentry_id(entry, "conversation")
        before = deepcopy(dict(entry.subentries[subentry_id].data))
        agent = conversation.async_get_agent(hass, entry.entry_id)
        await agent._guest_mode.async_update_trusted(indefinite=True)
        for entity_id in ("light.guest", "lock.private"):
            hass.states.async_set(entity_id, "on", {"friendly_name": entity_id})
            async_expose_entity(hass, conversation.DOMAIN, entity_id, True)
        exposed = get_exposed_entities(hass)
        assert {"light.guest", "lock.private"} <= {
            item["entity_id"] for item in exposed
        }

        def prompt():
            options = entry.subentries[subentry_id].data
            active = conversation.async_get_agent(hass, entry.entry_id)._guest_mode
            policy = resolve_guest_policy(
                hass, options, active, exposed_entities=exposed
            )
            rendered = render_effective_prompt(
                hass,
                options,
                exposed_entities=exposed,
                current_device_id=None,
                user_input=SimpleNamespace(text="hello"),
                skills=[],
                guest_policy=policy,
            )
            return policy, rendered.text

        prior_policy, text = prompt()
        assert "light.guest" in text and "lock.private" not in text
        client = await _client(
            hass, hass_ws_client, user_id="scoped-write-admin", owner=True
        )
        current = await _raw_call(
            client,
            entry_id=entry.entry_id,
            subentry_id=subentry_id,
            section="configuration",
            action="get",
        )
        assert current["success"]
        changed = await _raw_call(
            client,
            entry_id=entry.entry_id,
            subentry_id=subentry_id,
            section="settings",
            action="update",
            settings={"archive_retention_days": 7},
            revision=current["result"]["revision"],
        )
        assert changed["success"], changed
        await _settle_entry(hass, entry)
        assert dict(entry.subentries[subentry_id].data) == {
            **before,
            "archive_retention_days": 7,
        }
        policy, text = prompt()
        assert "lock.private" not in text
        assert policy.readable_entity_ids == prior_policy.readable_entity_ids
        assert (policy.shared_memory_read, policy.shared_memory_write) == (True, False)

        before = deepcopy(dict(entry.subentries[subentry_id].data))
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DISABLE_FUNCTION_TOOLS,
            {
                "config_entry": entry.entry_id,
                "agent_id": subentry_id,
                "functions": ["probe"],
            },
            blocking=True,
        )
        await _settle_entry(hass, entry)
        after = dict(entry.subentries[subentry_id].data)
        assert {key: value for key, value in after.items() if key != "functions"} == {
            key: value for key, value in before.items() if key != "functions"
        }
        assert not management_ui.configured_function_tools_from_data(after)[0][
            "enabled"
        ]
    finally:
        await _unload_entry(hass, entry)


@pytest.mark.parametrize("advanced", [False, True])
async def test_sparse_ai_task_public_reconfigure_preserves_raw_omissions(
    hass, advanced
):
    raw = {
        "chat_model": "gpt-5.6",
        "api_mode": "responses",
        "future_extension": {"untouched": [0, None]},
    }
    entry = _make_entry(ai_task_data=raw)
    await _setup_entry(hass, entry)
    try:
        subentry_id = _subentry_id(entry, "ai_task_data")
        before = deepcopy(dict(entry.subentries[subentry_id].data))
        shown = await entry.start_subentry_reconfigure_flow(hass, subentry_id)
        assert shown["type"] is FlowResultType.FORM
        edits = {"max_tokens": 777}
        if advanced:
            edits["advanced_options"] = True
        result = await hass.config_entries.subentries.async_configure(
            shown["flow_id"], edits
        )
        expected = {**before, **edits}
        if advanced:
            assert result["step_id"] == "advanced"
            result = await hass.config_entries.subentries.async_configure(
                result["flow_id"], {"shorten_tool_call_id": False}
            )
            expected["shorten_tool_call_id"] = False
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        await _settle_entry(hass, entry)
        assert dict(entry.subentries[subentry_id].data) == expected
        assert (
            not {"guest_policy_version", "reasoning_effort", "temperature", "top_p"}
            & expected.keys()
        )
    finally:
        await _unload_entry(hass, entry)
