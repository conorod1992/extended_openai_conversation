"""Follow-up findings through native HA services, storage and translations."""

import pytest
from pytest_homeassistant_custom_component.common import MockUser
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers.translation import _TranslationCache

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses import transfer
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider
from tests_real_ha.test_request_rules_script_semantics import _local
from tests_real_ha.test_service_registry_acceptance import _response_service_call


@pytest.mark.parametrize("child_service", ["conversation.process", f"{DOMAIN}.process"])
async def test_standard_child_service_does_not_poison_parent_response(
    hass, monkeypatch, child_service
):
    child = await _agent(hass, title="Child")
    await child._request_rules.async_create(
        _local(
            [{"variables": {"audit": "child"}}],
            phrase="child request",
            success="Child local answer",
        )
    )
    async_expose_entity(hass, "conversation", child.entity_id, True)
    tool = {
        "spec": {
            "name": "delegate",
            "description": "Ask the child",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": child_service,
                    "data": {
                        "agent_id": child.entity_id,
                        "text": "child request",
                        "language": "en",
                    },
                    "response_variable": "child_answer",
                }
            ],
        },
    }
    parent = await _agent(hass, title="Parent", **{CONF_FUNCTION_TOOLS: [tool]})
    _provider(monkeypatch, child, [])
    sent = _provider(
        monkeypatch,
        parent,
        [
            {
                "index": 0,
                "id": "delegate-call",
                "type": "function",
                "function": {"name": "delegate", "arguments": "{}"},
            },
            "Parent provider answered",
        ],
    )
    user = MockUser(id="nested-owner", name="Nested owner", is_owner=True).add_to_hass(
        hass
    )
    result = await _response_service_call(
        hass,
        "process",
        {"text": "Ask the child", "agent_id": parent.entity_id},
        user_id=user.id,
    )
    assert result["successful"] is True, result
    assert result["response"] == "Parent provider answered", result
    assert len(sent) == 2
    assert result["handled_locally"] is False
    assert not result.get("matched_rule")
    assert len(child._usage.runs) == 1 and child._usage.runs[0].request_count == 0


async def test_native_saved_rule_exports_no_encoded_credentials(hass):
    agent = await _agent(hass)
    secret = "synthetic-audit-token-do-not-use"
    await agent._request_rules.async_create(
        _local(
            [{"action": "rest_command.report_measurement", "data": {"api_key": secret}}]
        )
    )
    saved = await agent._request_rules.async_backup_data()
    document = transfer._new_transfer_document(agent.entry, agent.subentry, "setup")
    document["sections"] = {"configuration": {}, "request_rules": saved}
    assert secret not in transfer.finalize_setup_export(document)["json"]
    assert secret in saved["rules"][0]["action"]["canonical_signature"]
    assert await agent._request_rules.async_backup_data() == saved


@pytest.mark.parametrize("language", ["en", "de"])
async def test_runtime_retirement_translation_and_english_fallback(hass, language):
    cache = _TranslationCache(hass)
    assert await cache.async_fetch(language, "config", {DOMAIN})
    issues = await cache.async_fetch(language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.retired_model."
    assert issues[prefix + "title"]
    assert all(
        "{" + name + "}" in issues[prefix + "description"]
        for name in ("assistant", "model", "shutdown_at")
    )


@pytest.mark.parametrize("child_service", ["conversation.process", f"{DOMAIN}.process"])
@pytest.mark.parametrize("loading_mode", ["always", "on_demand"])
async def test_nested_rule_uses_callee_group_and_restores_parent_group(
    hass, monkeypatch, child_service, loading_mode
):
    """Real Rule Function dispatch must not compare another agent's Groups."""
    import json

    from custom_components.extended_openai_conversation_responses.const import (
        CONF_FUNCTION_GROUPS,
        SERVICE_CALL_FUNCTION,
    )

    def template(name, value):
        return {
            "spec": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {
                "type": "template",
                "value_template": value,
                "parse_result": False,
            },
        }

    def group(group_id, names, mode="always"):
        return {
            "id": group_id,
            "name": group_id,
            "description": group_id,
            "enabled": True,
            "loading_mode": mode,
            "functions": names,
        }

    child = await _agent(
        hass,
        title="Grouped child",
        **{
            CONF_FUNCTION_TOOLS: [template("surface_value_a", "child-only-value")],
            CONF_FUNCTION_GROUPS: [
                group("child-group", ["surface_value_a"], loading_mode)
            ],
        },
    )
    effects = []

    async def record(call):
        effects.append(call.data["value"])

    hass.services.async_register("nested_group_probe", "record", record)
    await child._request_rules.async_create(
        _local(
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {
                        "function": "surface_value_a",
                        "arguments": {},
                        "result_alias": "answer",
                    },
                },
                {"action": "nested_group_probe.record", "data": {"value": "{answer}"}},
            ],
            phrase="grouped child request",
            success="Child value {answer}",
            failure="Child function failed",
        )
    )
    async_expose_entity(hass, "conversation", child.entity_id, True)
    child_sent = _provider(monkeypatch, child, [])
    delegate = template("delegate", "unused")
    delegate["function"] = {
        "type": "script",
        "sequence": [
            {
                "action": child_service,
                "data": {
                    "agent_id": child.entity_id,
                    "text": "grouped child request",
                    "language": "en",
                },
                "response_variable": "child_answer",
            },
            {"variables": {"_function_result": "{{ child_answer }}"}},
        ],
    }
    parent = await _agent(
        hass,
        title="Grouped parent",
        **{
            CONF_FUNCTION_TOOLS: [
                delegate,
                template("parent_value", "parent-only-value"),
            ],
            CONF_FUNCTION_GROUPS: [group("parent-group", ["delegate", "parent_value"])],
        },
    )
    parent_sent = _provider(
        monkeypatch,
        parent,
        [
            {
                "index": 0,
                "id": name + "-call",
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
            for name in ("delegate", "parent_value")
        ]
        + ["Parent completed"],
    )
    user = MockUser(id="group-owner", name="Group owner", is_owner=True).add_to_hass(
        hass
    )
    direct = await _response_service_call(
        hass,
        "process",
        {"text": "grouped child request", "agent_id": child.entity_id},
        user_id=user.id,
    )
    assert direct["successful"] is True, direct
    assert direct["response"] == "Child value child-only-value"
    result = await _response_service_call(
        hass,
        "process",
        {"text": "Delegate and check parent", "agent_id": parent.entity_id},
        user_id=user.id,
    )
    assert result["successful"] is True, result
    assert result["response"] == "Parent completed"
    assert effects == ["child-only-value", "child-only-value"]
    assert child_sent == []
    assert len(parent_sent) == 3
    retained = json.dumps(parent_sent[-1]["messages"])
    assert "Child value child-only-value" in retained
    assert "parent-only-value" in retained
    assert "Child function failed" not in retained


@pytest.mark.parametrize("quarantined_tool", [False, True])
async def test_group_repair_reaches_registered_websocket_handler(
    hass, hass_ws_client, monkeypatch, quarantined_tool
):
    from copy import deepcopy
    from unittest.mock import AsyncMock
    from custom_components.extended_openai_conversation_responses import (
        management_ui as ui,
    )
    from tests_real_ha.test_management_backend_acceptance import (
        _admin_client,
        _management_call,
        _management_response,
    )

    tool = {
        "spec": {
            "name": "execute_services",
            "description": "Probe",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "OK"},
    }
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [tool]})
    broken_tool = deepcopy(tool)
    if quarantined_tool:
        broken_tool["spec"]["parameters"]["description"] = 123
    broken_group = {
        "id": "repair-group",
        "description": "Repair me",
        "loading_mode": "always",
        "enabled": True,
        "functions": ["execute_services"],
    }
    hass.config_entries.async_update_subentry(
        agent.entry,
        agent.subentry,
        data={
            **agent.subentry.data,
            CONF_FUNCTION_TOOLS: [broken_tool],
            "function_groups": [broken_group],
        },
    )
    client = await _admin_client(hass, hass_ws_client)
    before = await _management_call(
        client, entry=agent.entry, section="configuration", action="get"
    )
    assert before.get("function_repair")
    raw_tools = deepcopy(agent.subentry.data[CONF_FUNCTION_TOOLS])
    dispatch = AsyncMock(wraps=ui.async_management_command)
    monkeypatch.setattr(ui, "async_management_command", dispatch)
    replacement = {**broken_group, "name": "Repaired", "functions": []}
    repaired = await _management_response(
        client,
        entry=agent.entry,
        section="tools",
        action="save_group",
        original_id="repair-group",
        repair=True,
        group=replacement,
    )
    assert repaired["success"] is True, repaired
    assert repaired["result"].get("valid", True), repaired
    assert dispatch.await_count == 1
    assert agent.subentry.data["function_groups"][0]["name"] == "Repaired"
    assert agent.subentry.data[CONF_FUNCTION_TOOLS] == raw_tools
    missing_id = await _management_response(
        client,
        entry=agent.entry,
        section="tools",
        action="save_group",
        repair=True,
        group=replacement,
    )
    assert missing_id["success"] is False
    assert missing_id["error"]["code"] == "invalid_request"
    assert "original_id" in missing_id["error"]["message"]
    assert dispatch.await_count == 2
    invalid_flag = await _management_response(
        client,
        entry=agent.entry,
        section="tools",
        action="save_group",
        original_id="repair-group",
        repair="true",
        group=replacement,
    )
    assert invalid_flag["success"] is False
    assert invalid_flag["error"]["code"] == "invalid_format"
    assert dispatch.await_count == 2
    after = await _management_call(
        client, entry=agent.entry, section="configuration", action="get"
    )
    assert after["config"]["function_groups"][0]["name"] == "Repaired"

