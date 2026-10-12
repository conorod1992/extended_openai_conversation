"""Audit fixes exercised through native HA lifecycle, scripts and provider wire."""

from copy import deepcopy
from datetime import timedelta
import json

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    async_get_quiet_hours,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
from tests_real_ha.test_entry_point_contract_matrix import _management
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_tool_result,
    _knowledge_agent,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
)
from tests_real_ha.test_request_rules_script_semantics import _local


async def test_model_case_distinct_temporary_memory_survives_reload(hass, monkeypatch):
    from tests_real_ha.test_temporary_memory_lifecycle import (
        _agent as temporary_agent,
        _say as temporary_say,
    )

    owner = MockUser(id="audit-memory-owner", name="Audit owner").add_to_hass(hass)
    agent = await temporary_agent(hass)
    contents = ["/config/Reports.csv", "/config/reports.csv"]
    expires = (dt_util.utcnow() + timedelta(hours=1)).isoformat()
    replies = []
    for index, content in enumerate(contents):
        replies.extend(
            [
                _chat_sse_tool_call(
                    f"fact-{index}",
                    "temporary_memory_add",
                    {"content": content, "category": "paths", "expires_at": expires},
                ),
                _chat_sse_text("Remembered."),
            ]
        )
    _install_wire(monkeypatch, agent, replies)
    for _ in contents:
        assert (
            _speech(
                await temporary_say(hass, agent, owner.id, "Remember this file path")
            )
            == "Remembered."
        )
    assert await hass.config_entries.async_reload(agent.entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    records = await agent._temporary_memory.async_active(
        f"user:{owner.id}", owner_scope_id=f"user:{owner.id}"
    )
    assert sorted(record.content for record in records) == sorted(contents)


async def test_quiet_registry_rename_disable_and_last_removal(hass):
    agent = await _agent(hass)
    manager = await async_get_quiet_hours(hass)
    registry = er.async_get(hass)
    original = manager.snapshot()["state_entity_id"]
    assert hass.states.get(original) is not None
    renamed = "binary_sensor.audit_renamed_quiet_hours"
    registry.async_update_entity(original, new_entity_id=renamed)
    await hass.async_block_till_done()
    assert hass.states.get(original) is None
    assert hass.states.get(renamed) is not None
    registry.async_update_entity(renamed, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    assert hass.states.get(renamed) is None
    registry.async_update_entity(renamed, disabled_by=None)
    await hass.async_block_till_done()
    assert hass.states.get(renamed) is not None
    await hass.config_entries.async_remove(agent.entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(renamed) is None
    assert hass.states.get(renamed) is None


async def test_saved_reasoning_defaults_validate_through_management(hass):
    agent = await _agent(
        hass,
        chat_model="gpt-5.2-pro",
        api_mode="responses",
        service_tier="default",
        reasoning_effort="medium",
    )
    data = dict(agent.subentry.data)
    data.pop("reasoning_effort", None)
    hass.config_entries.async_update_subentry(agent.entry, agent.subentry, data=data)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    command = await _management(hass, agent)
    current = await command("get")
    saved = await command(
        "save", revision=current["revision"], config={"max_tokens": 750}
    )
    assert saved["valid"], saved
    assert saved["config"].get("reasoning_effort") in {None, "medium", "high", "xhigh"}
    assert (await command("validate", config=saved["config"]))["valid"]
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "target",
    ["light.{room}", {"value_from": "slot", "slot": "room"}],
    ids=["embedded-capture", "binding-object"],
)
async def test_legacy_capture_executes_after_store_migration_and_reload(hass, target):
    agent = await _agent(hass)
    hass.states.async_set("light.kitchen", "on")
    async_expose_entity(hass, "conversation", "light.kitchen", True)
    effects = []

    async def turn_off(call):
        effects.extend(call.data["entity_id"])

    hass.services.async_register("light", "turn_off", turn_off)
    phrase = "turn off {room}"
    text = "turn off kitchen" if isinstance(target, str) else "turn off light.kitchen"
    rule = _local(
        [{"domain": "light", "service": "turn_off", "target": {"entity_id": target}}],
        phrase=phrase,
        match_type="sentence_pattern",
    )
    rule["id"] = "legacy-target"
    await agent._request_rules._store.async_save({"rules": [rule]})
    assert await hass.config_entries.async_reload(agent.entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert agent._request_rules.snapshot()["diagnostics"] == {}
    assert _speech(await _say(hass, agent, text)) == "Done"
    assert effects == ["light.kitchen"]


async def test_reserved_capture_retains_authored_rule_across_reload(hass):
    agent = await _agent(hass)
    rule = _local(
        [{"action": "homeassistant.update_entity", "target": {"entity_id": "light.x"}}],
        phrase="ask {request}",
        match_type="sentence_pattern",
    )
    rule.update(id="legacy-input", ai_input_mode="capture", ai_input_capture="request")
    rule["action"]["continue_to_ai"] = True
    await agent._request_rules._store.async_save({"rules": [rule]})
    for _ in range(2):
        assert await hass.config_entries.async_reload(agent.entry.entry_id)
        await hass.async_block_till_done()
        agent = conversation.async_get_agent(hass, agent.entry.entry_id)
        snapshot = agent._request_rules.snapshot()
        assert snapshot["rules"][0]["ai_input_capture"] == "request"
        assert "legacy-input" in snapshot["diagnostics"]
        assert agent._request_rules.match("ask hello") is None


@pytest.mark.parametrize("key", ["service_template", "service", "action"])
async def test_management_native_alias_and_chained_wording_execute(hass, key):
    agent = await _agent(hass)
    owner = await hass.auth.async_create_user("Rule audit", group_ids=["system-admin"])
    effects = []

    async def record(call):
        effects.append(call.data["message"])

    hass.services.async_register("rule_probe", "record", record)
    await agent._request_rules.async_set_wording_groups(
        [
            {"canonical": "tv", "alternatives": ["television"]},
            {"canonical": "turn off television", "alternatives": ["bedtime"]},
        ]
    )
    rule = _local(
        [{key: "{{ 'rule_probe.record' }}", "data": {"message": "executed"}}],
        phrase="turn off tv",
    )
    saved = await async_management_command(
        hass,
        owner.id,
        True,
        {
            "section": "request_rules",
            "action": "create",
            "entry_id": agent.entry.entry_id,
            "subentry_id": agent.subentry.subentry_id,
            "rule": rule,
        },
    )
    assert saved["rule"]["id"]
    assert _speech(await _say(hass, agent, "bedtime")) == "Done"
    assert effects == ["executed"]


async def test_escaped_knowledge_section_remains_complete_on_provider_wire(
    hass, monkeypatch
):
    agent = await _knowledge_agent(hass, "chat_completions")
    content = '"\\\n' * 15_000
    source = await agent._knowledge.async_create("Escaped reference", "", content)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "escaped-page",
                "knowledge_get",
                {"source_id": source.source_id, "max_characters": 20_000},
            ),
            _chat_sse_text("Read the first page."),
        ],
    )
    assert (
        _speech(await _say(hass, agent, "Read the escaped reference"))
        == "Read the first page."
    )
    page = _chat_tool_result(wire.requests[1]["body"], "escaped-page")
    expected = (await agent._knowledge.async_get(source.source_id)).content
    assert page["content"] == expected[: page["returned_characters"]]
    assert page["has_more"]
    assert page["next_start_character"] == len(page["content"])


async def test_guest_partial_service_batch_stops_provider_loop(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses.const import (
        DEFAULT_CONF_FUNCTION_TOOLS,
    )

    agent = await _agent(
        hass,
        functions=[deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])],
        function_tool_error_recovery=True,
        guest_policy_version=2,
        guest_function_policy="on",
    )
    assert await async_setup_component(
        hass, "counter", {"counter": {"audit_guest_effect": {"initial": 0}}}
    )
    await hass.async_block_till_done()
    entity_id = "counter.audit_guest_effect"
    async_expose_entity(hass, "conversation", entity_id, True)
    await agent._guest_mode.async_update_trusted(indefinite=True)

    async def fail(_call):
        raise HomeAssistantError("private backend detail")

    hass.services.async_register("counter", "audit_fail", fail)
    arguments = {
        "list": [
            {
                "domain": "counter",
                "service": "increment",
                "service_data": {"entity_id": [entity_id]},
            },
            {
                "domain": "counter",
                "service": "audit_fail",
                "service_data": {"entity_id": [entity_id]},
            },
        ]
    }
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("partial", "execute_services", arguments),
            _chat_sse_tool_call("unsafe-retry", "execute_services", arguments),
        ],
    )
    result = await _say(hass, agent, "Run the counter batch")
    assert result.response.error_code is not None
    assert "private backend" not in json.dumps(result.as_dict())
    assert hass.states.get(entity_id).state == "1"
    assert len(wire.requests) == 1


async def test_native_rule_management_ignores_unrelated_quarantined_schema(hass):
    agent = await _agent(hass)
    broken = {
        "spec": {
            "name": "broken",
            "description": "Legacy broken metadata",
            "parameters": {"type": "object", "required": "invalid"},
        },
        "function": {"type": "template", "value_template": "unused"},
    }
    data = {**agent.subentry.data, "functions": [broken]}
    hass.config_entries.async_update_subentry(agent.entry, agent.subentry, data=data)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    owner = await hass.auth.async_create_user(
        "Quarantine audit", group_ids=["system-admin"]
    )

    async def command(action, **values):
        return await async_management_command(
            hass,
            owner.id,
            True,
            {
                "section": "request_rules",
                "action": action,
                "entry_id": agent.entry.entry_id,
                "subentry_id": agent.subentry.subentry_id,
                **values,
            },
        )

    saved = await command(
        "create", rule=_local([{"set_conversation_response": "Native only"}])
    )
    disabled = {**saved["rule"], "enabled": False}
    assert (await command("update", rule_id=disabled["id"], rule=disabled))["rule"][
        "enabled"
    ] is False
    assert await hass.config_entries.async_reload(agent.entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert agent._request_rules.snapshot()["rules"][0]["enabled"] is False


@pytest.mark.usefixtures("socket_enabled")
async def test_large_rest_json_reaches_provider_without_full_event_loop_parse(
    hass, monkeypatch
):
    from aiohttp import web

    from custom_components.extended_openai_conversation_responses import (
        model_tool_results,
    )

    value = json.dumps({"content": "x" * 9_000_000})
    app = web.Application()

    async def respond(_request):
        return web.Response(text=value, content_type="application/json")

    app.router.add_get("/large", respond)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        tool = {
            "spec": {
                "name": "large_rest",
                "description": "Fetch reference",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "rest", "resource": f"http://127.0.0.1:{port}/large"},
        }
        agent = await _agent(hass, functions=[tool])
        wire = _install_wire(
            monkeypatch,
            agent,
            [
                _chat_sse_tool_call("large-rest", "large_rest", {}),
                _chat_sse_text("Read the bounded reference."),
            ],
        )
        original = json.loads

        def guard(value, *args, **kwargs):
            assert not isinstance(value, str) or len(value) < 1_000_000, (
                "large JSON must not be parsed on the event loop"
            )
            return original(value, *args, **kwargs)

        monkeypatch.setattr(model_tool_results.json, "loads", guard)
        assert (
            _speech(await _say(hass, agent, "Fetch the large reference"))
            == "Read the bounded reference."
        )
        message = next(
            item
            for item in wire.requests[1]["body"]["messages"]
            if item.get("role") == "tool"
        )
        result = original(message["content"])["result"]
        assert len(result) == 32_000
        assert result.endswith(
            f"...[tool result truncated] original_characters={len(value)}"
        )
    finally:
        await runner.cleanup()
