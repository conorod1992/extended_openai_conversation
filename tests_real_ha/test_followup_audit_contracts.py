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
