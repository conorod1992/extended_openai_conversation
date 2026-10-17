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


@pytest.mark.parametrize(
    "variant", ["manual", "legacy-values", "legacy-empty", "yaml-null"]
)
@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("operation", ["group", "function", "knowledge"])
async def test_scoped_save_effective_configuration_matches_all_read_paths(
    hass, hass_ws_client, variant, warm, operation
):
    from custom_components.extended_openai_conversation_responses import (
        agent_config,
        management_function_repair as projections,
    )
    from custom_components.extended_openai_conversation_responses.const import (
        LEGACY_DEFAULT_PROMPTS,
    )
    from custom_components.extended_openai_conversation_responses.memory import (
        memory_enabled,
    )

    raw = {
        "chat_model": "gpt-5.6",
        "functions": "[]",
        "memory_mode": "manual",
        "future_extension": {"keep": [None, False]},
    }
    if variant == "legacy-values":
        raw.pop("memory_mode")
        raw.update(
            memory_enabled=True,
            memory_auto_create=False,
            max_tokens="1234",
            prompt=next(iter(LEGACY_DEFAULT_PROMPTS)),
            usage_request_retention_days="7",
            local_intent_exclusions=[" light.test ", "light.test"],
            exposed_entity_attributes={
                "registry:probe": ["z", "a", "a"],
                "registry:empty": [],
            },
        )
    elif variant == "legacy-empty":
        raw["functions"] = []
    elif variant == "yaml-null":
        raw["functions"] = "null"
    entry = _make_entry(conversation_data=deepcopy(raw))
    await _setup_entry(hass, entry)
    try:
        subentry_id = _subentry_id(entry, "conversation")
        subentry = entry.subentries[subentry_id]
        before = deepcopy(dict(subentry.data))
        projections.discard_persisted_config_projection(subentry)
        client = await _client(
            hass, hass_ws_client, user_id="projection-invariant-admin", owner=True
        )

        async def read():
            response = await _raw_call(
                client,
                entry_id=entry.entry_id,
                subentry_id=subentry_id,
                section="configuration",
                action="get",
            )
            assert response["success"], response
            return response["result"]["config"]

        if warm:
            await read()
        revision = projections.persisted_config_projection(subentry).revision
        if operation == "group":
            names = [
                tool["spec"]["name"]
                for tool in agent_config.parse_configured_function_tools(before) or []
            ]
            section, action = "tools", "save_group"
            fields = {
                "group": {
                    "id": "projection-group",
                    "name": "Projection",
                    "description": "Projection group",
                    "loading_mode": "always",
                    "functions": names[:1],
                }
            }
            owned = {"functions", "function_groups"}
        elif operation == "function":
            section, action = "tools", "save"
            fields = {
                "tool": {
                    "spec": {
                        "name": "projection_probe",
                        "description": "Probe",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {"type": "native", "name": "get_user_from_user_id"},
                }
            }
            owned = {"functions", "function_groups"}
        else:
            section, action = "knowledge", "set_enabled"
            fields = {"enabled": True}
            owned = {"knowledge_enabled"}
        response = await _raw_call(
            client,
            entry_id=entry.entry_id,
            subentry_id=subentry_id,
            section=section,
            action=action,
            revision=revision,
            **fields,
        )
        assert response["success"], response
        subentry = entry.subentries[subentry_id]
        assert {k: v for k, v in subentry.data.items() if k not in owned} == {
            k: v for k, v in before.items() if k not in owned
        }
        immediate = await read()
        assert immediate == agent_config.agent_config_snapshot(dict(subentry.data))
        assert immediate["memory_enabled"] == memory_enabled(subentry.data) is True
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent._memory is not None
        assert [tool["spec"]["name"] for tool in immediate["functions"]] == [
            tool["spec"]["name"]
            for tool in agent_config.configured_function_tools_from_data(subentry.data)
        ]
        projections.discard_persisted_config_projection(subentry)
        assert await read() == immediate
        projections.discard_persisted_config_projection(subentry)
        await projections.async_prewarm_persisted_config_projection(
            hass, entry, subentry
        )
        assert await read() == immediate
    finally:
        await _unload_entry(hass, entry)


@pytest.mark.parametrize("saved", [[], "null"], ids=["legacy-empty", "yaml-null"])
@pytest.mark.parametrize("rollback", [False, True])
async def test_configuration_projection_round_trip_through_restore_and_rollback(
    hass, hass_ws_client, monkeypatch, saved, rollback
):
    from custom_components.extended_openai_conversation_responses import (
        agent_config,
        backup,
        management_function_repair as projections,
        transfer,
    )
    from custom_components.extended_openai_conversation_responses.memory import (
        memory_enabled,
    )

    entry = _make_entry(
        conversation_data={
            "chat_model": "gpt-5.6",
            "functions": saved,
            "memory_enabled": True,
            "memory_auto_create": False,
        }
    )
    await _setup_entry(hass, entry)
    try:
        subentry_id = _subentry_id(entry, "conversation")
        subentry = entry.subentries[subentry_id]
        names = [
            tool["spec"]["name"]
            for tool in agent_config.parse_configured_function_tools(subentry.data)
            or []
        ]
        client = await _client(
            hass, hass_ws_client, user_id="projection-restore-admin", owner=True
        )
        before = await _raw_call(
            client,
            entry_id=entry.entry_id,
            subentry_id=subentry_id,
            section="configuration",
            action="get",
        )
        assert before["success"]
        snapshot = await backup.async_collect_backup_snapshot(hass, entry, subentry)
        recovered = backup.inspect_backup(snapshot, subentry_id)
        assert [
            tool["spec"]["name"]
            for tool in agent_config.parse_configured_function_tools(recovered.config)
            or []
        ] == names
        assert memory_enabled(recovered.config)
        document = transfer._new_transfer_document(entry, subentry, "custom")
        document["sections"][transfer.SECTION_KNOWLEDGE] = {"sources": []}
        if rollback:
            knowledge = conversation.async_get_agent(hass, entry.entry_id)._knowledge
            original = knowledge.async_replace_backup
            calls = 0

            async def fail_once(records):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("injected projection rollback")
                await original(records)

            monkeypatch.setattr(knowledge, "async_replace_backup", fail_once)
            with pytest.raises(
                backup.BackupError, match="previous agent state was recovered"
            ):
                await transfer.async_restore_transfer(hass, entry, subentry, document)
            assert calls == 2
        else:
            await transfer.async_restore_transfer(hass, entry, subentry, document)
        subentry = entry.subentries[subentry_id]
        assert "reasoning_effort" not in subentry.data
        assert "guest_policy_version" not in subentry.data
        result = await _raw_call(
            client,
            entry_id=entry.entry_id,
            subentry_id=subentry_id,
            section="configuration",
            action="get",
        )
        assert result["success"], result
        shown = result["result"]["config"]
        assert shown["memory_enabled"] == memory_enabled(subentry.data) is True
        assert [tool["spec"]["name"] for tool in shown["functions"]] == names
        assert shown == agent_config.agent_config_snapshot(dict(subentry.data))
        projections.discard_persisted_config_projection(subentry)
        await projections.async_prewarm_persisted_config_projection(
            hass, entry, subentry
        )
        repeated = await _raw_call(
            client,
            entry_id=entry.entry_id,
            subentry_id=subentry_id,
            section="configuration",
            action="get",
        )
        assert repeated["success"] and repeated["result"]["config"] == shown
    finally:
        await _unload_entry(hass, entry)


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
async def test_legacy_numeric_configuration_matches_actual_provider_wire(
    hass, hass_ws_client, monkeypatch, api
):
    from custom_components.extended_openai_conversation_responses import agent_config
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _install_wire,
        _responses_sse_text,
    )

    agent = await _agent(
        hass,
        chat_model="gpt-4.1",
        api_mode=api,
        temperature="0.25",
        top_p="0.9",
        max_tokens="1234",
        max_function_calls_per_conversation=0,
    )
    original = deepcopy(dict(agent.subentry.data))
    assert original["temperature"] == "0.25" and original["top_p"] == "0.9"
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            (_responses_sse_text if api == "responses" else _chat_sse_text)(
                "Numeric configuration ready"
            )
        ],
    )
    result = await _say(hass, agent, "Check numeric configuration")
    assert result.response.error_code is None
    assert len(wire.requests) == 1
    body = wire.requests[0]["body"]
    client = await _client(
        hass, hass_ws_client, user_id="numeric-projection-admin", owner=True
    )
    read = await _raw_call(
        client,
        entry_id=agent.entry.entry_id,
        subentry_id=agent.subentry.subentry_id,
        section="configuration",
        action="get",
    )
    assert read["success"], read
    shown = read["result"]["config"]
    assert shown == agent_config.agent_config_snapshot(dict(agent.subentry.data))
    for key in ("temperature", "top_p"):
        assert body[key] == shown[key]
        assert type(body[key]) is type(shown[key]) is float
    assert dict(agent.subentry.data) == original
