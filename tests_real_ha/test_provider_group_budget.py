"""Effective tool contracts through the real conversation group's loader."""

import json

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    FUNCTION_GROUP_LOADER_TOOL_NAME,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests.test_function_groups import _group, _tool
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _management_call,
    _management_response,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _responses_sse_text,
    _responses_sse_tool_call,
    _say,
    _speech,
)


@pytest.mark.parametrize("provider", ["openai", "azure", "compatible"])
@pytest.mark.parametrize("configured_count", [128, 129])
async def test_chat_completions_blocks_oversized_saved_catalogue_and_recovers(
    hass, hass_ws_client, monkeypatch, provider, configured_count
):
    """Count the lifecycle tool in Preview, save validation, and SDK dispatch."""
    tools = [_tool(f"tool_{index}") for index in range(configured_count)]
    options = {
        "chat_model": "gpt-4o",
        "api_mode": "chat_completions",
        "functions": tools,
        "function_groups": [],
        "archive_enabled": False,
        "guest_mode_enabled": False,
        "memory_mode": "off",
        "temporary_memory": "off",
    }
    if provider == "azure":
        options["azure_model"] = "gpt-4o"
    endpoints = {
        "azure": "https://resource.openai.azure.com",
        "compatible": "https://compatible.example/v1",
    }
    entry = _make_entry(
        include_ai_task=False,
        base_url=endpoints.get(provider),
        data={
            "api_provider": "azure" if provider == "azure" else "openai",
            "api_version": "2024-10-21",
        },
        conversation_options=options,
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            (
                400,
                {
                    "error": {
                        "message": "Too many tools",
                        "type": "invalid_request_error",
                        "param": "tools",
                        "code": "array_above_max_length",
                    }
                },
            )
        ],
    )
    failed = await _say(hass, agent)
    assert failed.response.error_code is not None
    assert [len(item["body"]["tools"]) for item in wire.requests] == []

    client = await _admin_client(hass, hass_ws_client)
    before = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    preview = await _management_response(
        client, entry=entry, section="configuration", action="request_preview"
    )
    assert not preview["success"] and "128-tool limit" in str(preview)
    repaired = await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="save",
        revision=before["revision"],
        config={"functions": tools[:127]},
    )
    await hass.async_block_till_done()
    assert repaired["valid"]
    preview = await _management_response(
        client, entry=entry, section="configuration", action="request_preview"
    )
    assert preview["success"]
    rejected_memory = await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="save",
        revision=repaired["revision"],
        config={"memory_mode": "manual"},
    )
    assert not rejected_memory["valid"]
    assert "128-tool limit" in str(rejected_memory["errors"])
    rejected = await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="save",
        revision=repaired["revision"],
        config={"functions": tools},
    )
    assert not rejected["valid"] and "128-tool limit" in str(rejected["errors"])

    agent = conversation.async_get_agent(hass, entry.entry_id)
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Recovered")])
    assert _speech(await _say(hass, agent)) == "Recovered"
    [request] = wire.requests
    names = {item["function"]["name"] for item in request["body"]["tools"]}
    assert names == {
        "start_fresh_conversation",
        *(tool["spec"]["name"] for tool in tools[:127]),
    }


@pytest.mark.parametrize(
    "provider,base_url,mode,limited",
    [
        pytest.param(
            "azure",
            "https://resource.openai.azure.com",
            "chat_completions",
            True,
            id="azure-chat",
        ),
        pytest.param("openai", None, "chat_completions", True, id="openai-chat"),
        pytest.param("openai", None, "responses", False, id="openai-responses"),
        pytest.param(
            "openai",
            "https://compatible.example/v1",
            "chat_completions",
            True,
            id="compatible-chat",
        ),
    ],
)
async def test_group_loading_obeys_effective_provider_budget(
    hass, monkeypatch, provider, base_url, mode, limited
):
    tools = [_tool(f"tool_{index}") for index in range(160)]
    groups = [
        _group("first", [tool["spec"]["name"] for tool in tools[:80]]),
        _group("second", [tool["spec"]["name"] for tool in tools[80:]]),
    ]
    entry = _make_entry(
        include_ai_task=False,
        base_url=base_url,
        data={"api_provider": provider, "api_version": "2024-10-21"},
        conversation_options={
            "functions": tools,
            "function_groups": groups,
            "api_mode": mode,
            "archive_enabled": False,
            "guest_mode_enabled": False,
            "chat_model": "gpt-4o",
            **({"azure_model": "gpt-4o"} if provider == "azure" else {}),
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    tool_call = (
        _chat_sse_tool_call if mode == "chat_completions" else _responses_sse_tool_call
    )
    text = _chat_sse_text if mode == "chat_completions" else _responses_sse_text
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            tool_call(
                "first-call", FUNCTION_GROUP_LOADER_TOOL_NAME, {"groups": ["first"]}
            ),
            tool_call(
                "second-call", FUNCTION_GROUP_LOADER_TOOL_NAME, {"groups": ["second"]}
            ),
            text("Finished"),
        ],
    )
    assert _speech(await _say(hass, agent)) == "Finished"
    first, second, final = [request["body"] for request in wire.requests]
    assert len(first["tools"]) == 2  # Lifecycle and group loader.
    assert len(second["tools"]) == 82
    assert len(final["tools"]) == (82 if limited else 161)
    assert ("128-tool limit" in json.dumps(final)) is limited


async def test_loaded_groups_are_reset_when_knowledge_tools_fill_the_budget(
    hass, monkeypatch
):
    tools = [_tool(f"tool_{index}") for index in range(127)]
    entry = _make_entry(
        include_ai_task=False,
        conversation_options={
            "chat_model": "gpt-4o",
            "api_mode": "chat_completions",
            "archive_enabled": False,
            "guest_mode_enabled": False,
            "memory_mode": "off",
            "temporary_memory": "off",
            "knowledge_enabled": True,
            "functions": tools,
            "function_groups": [
                _group("large", [tool["spec"]["name"] for tool in tools])
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "first-call", FUNCTION_GROUP_LOADER_TOOL_NAME, {"groups": ["large"]}
            ),
            _chat_sse_text("Finished"),
            _chat_sse_tool_call(
                "second-call", FUNCTION_GROUP_LOADER_TOOL_NAME, {"groups": ["large"]}
            ),
            _chat_sse_text("Still available"),
        ],
    )
    first = await _say(hass, agent)
    assert _speech(first) == "Finished"
    assert len(wire.requests[-1]["body"]["tools"]) == 128
    await agent._knowledge.async_create("Manual", "Test source", "New instructions.")

    reply = await conversation.async_converse(
        hass=hass,
        text="Continue the previous discussion",
        conversation_id=first.conversation_id,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(reply) == "Still available"
    assert reply.conversation_id == first.conversation_id
    follow_up, final = [request["body"] for request in wire.requests[2:]]
    names = {tool["function"]["name"] for tool in follow_up["tools"]}
    assert names == {
        FUNCTION_GROUP_LOADER_TOOL_NAME,
        "knowledge_search",
        "knowledge_list",
        "knowledge_get",
        "start_fresh_conversation",
    }
    assert "128-tool limit" in json.dumps(final)


async def test_fifth_group_fills_capacity_and_next_turn_reuses_it(hass, monkeypatch):
    tools = [_tool(f"fixed_{index}") for index in range(122)]
    tools.extend(_tool(f"member_{index}") for index in range(6))
    entry = _make_entry(
        include_ai_task=False,
        conversation_options={
            "chat_model": "gpt-4o",
            "api_mode": "chat_completions",
            "functions": tools,
            "function_groups": [
                _group(f"group_{index}", [f"member_{index}"]) for index in range(6)
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            *[
                _chat_sse_tool_call(
                    f"call_{index}",
                    FUNCTION_GROUP_LOADER_TOOL_NAME,
                    {"groups": [f"group_{index}"]},
                )
                for index in range(5)
            ],
            _chat_sse_text("Finished"),
            _chat_sse_text("Continued"),
        ],
    )
    result = await _say(hass, agent)
    assert _speech(result) == "Finished"
    followup = await conversation.async_converse(
        hass=hass,
        text="Continue",
        conversation_id=result.conversation_id,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(followup) == "Continued"
    sent = [request["body"] for request in wire.requests]
    assert [len(body["tools"]) for body in sent] == [124, 125, 126, 127, 128, 128, 128]
    for body in sent[-2:]:
        names = {tool["function"]["name"] for tool in body["tools"]}
        assert "member_4" in names
        assert "member_5" not in names
        assert FUNCTION_GROUP_LOADER_TOOL_NAME not in names
