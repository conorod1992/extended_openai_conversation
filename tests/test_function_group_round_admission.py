"""Provider-round capacity regressions for on-demand Function Groups."""

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation,
    entity,
    management_request_preview as preview,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONTINUE_CONVERSATION_CONDITIONAL,
    FUNCTION_GROUP_LOADER_TOOL_NAME,
)
from custom_components.extended_openai_conversation_responses.conversation_lifecycle import (
    begin_conversation_lifecycle,
    end_conversation_lifecycle,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
    FunctionToolAdmission,
    assemble_function_tools,
    load_function_groups,
)
from custom_components.extended_openai_conversation_responses.function_tool_policy import (
    CONTINUE_CONVERSATION_TOOL_NAME,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    ToolSnapshot,
    tool_snapshot_scope,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.request import (
    ProviderRequestSnapshot,
)
from custom_components.extended_openai_conversation_responses.scheduled_action_tools import (
    scheduled_action_tools,
)
from homeassistant.core import Context
from tests.test_function_groups import _group, _tool
from tests.test_tool_exchange_protocol import _chat_log, _entity, _function_call_stream


def _delay_tool(name):
    tool = _tool(name)
    tool["spec"]["parameters"]["properties"]["delay"] = {
        "type": "object",
        "properties": {"seconds": {"type": "number"}},
    }
    return tool


@pytest.mark.parametrize("offset", [-3, -1, 0, 1])
@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("recognized_delayed", [False, True])
async def test_live_group_load_and_preview_fit_the_actual_provider_round(
    hass, monkeypatch, offset, owner, recognized_delayed
):
    """Reserve Web Search/finalizer and admit schemas exactly as the runtime will."""
    cap = 8
    provider = ProviderRequestSnapshot(
        api_mode="responses",
        api_kwargs={"model": "test"},
        provider_tools=({"type": "web_search"},),
        tool_limit=cap,
    )
    monkeypatch.setattr(
        entity, "build_provider_request_snapshot", lambda *_a, **_k: provider
    )
    monkeypatch.setattr(
        preview, "build_provider_request_snapshot", lambda *_a, **_k: provider
    )
    monkeypatch.setattr(preview, "get_exposed_entities", lambda _: [])

    # Web Search and the finalizer are reserved on every round. For ordinary
    # requests, the group has one extra immediate member and the lifecycle tool
    # is present. The same offsets represent the actual boundary in each turn.
    delayed_count = cap - 2 + offset - 2 * int(not recognized_delayed)
    wanted = [_delay_tool(f"delay_{i}") for i in range(delayed_count)]
    wanted.append(_tool("immediate_member"))
    groups = [
        _group("wanted", [item["spec"]["name"] for item in wanted]),
        _group("later", ["next_delay"]),
        _group("immediate_only", ["other_immediate"]),
    ]
    options = agent_config_defaults()
    options.update(
        {
            "functions": [*wanted, _delay_tool("next_delay"), _tool("other_immediate")],
            "function_groups": groups,
            "continue_conversation": CONTINUE_CONVERSATION_CONDITIONAL,
            "archive_enabled": False,
            "memory_mode": "off",
            "temporary_memory": "off",
            "guest_mode_enabled": False,
            "current_datetime_enabled": False,
            "exposed_entities_enabled": False,
            "prompt": "Be helpful",
        }
    )
    wire = _entity(
        hass,
        [
            _function_call_stream(
                [("load", FUNCTION_GROUP_LOADER_TOOL_NAME, {"groups": ["wanted"]})]
            ),
            _function_call_stream(
                [
                    (
                        "final",
                        CONTINUE_CONVERSATION_TOOL_NAME,
                        {"response": "Done", "continue_conversation": False},
                    )
                ]
            ),
        ],
    )
    subentry = SimpleNamespace(subentry_id="agent-1", title="Agent", data=options)
    agent = conversation.ExtendedOpenAIAgentEntity(wire.entry, subentry)
    agent.hass = hass
    agent.entity_id = "conversation.test"
    agent._usage = None
    monkeypatch.setattr(
        agent, "_effective_guest_policy", GuestCapabilityPolicy.unrestricted
    )
    session = FunctionGroupRuntime().begin("conversation", 30)
    log = _chat_log(hass)
    snapshot = ToolSnapshot(require_positive_delay=recognized_delayed)
    turn = conversation._ACTIVE_TURN.set(
        replace(conversation._ACTIVE_TURN.get(), function_group_session=session)
    )
    caller = conversation._ACTIVE_LLM_CONTEXT.set(
        SimpleNamespace(context=Context(user_id="caller")) if owner else None
    )
    lifecycle = begin_conversation_lifecycle()
    try:
        with tool_snapshot_scope(snapshot):
            await agent._async_handle_chat_log(
                log,
                [],
                [],
                conditional_continue=True,
                function_tools_factory=agent._get_function_tools,
                function_group_loader=agent._load_function_groups,
            )
    finally:
        end_conversation_lifecycle(lifecycle)
        conversation._ACTIVE_LLM_CONTEXT.reset(caller)
        conversation._ACTIVE_TURN.reset(turn)

    first, second = [
        recorded.kwargs["tools"]
        for recorded in wire._client.responses.create.await_args_list
    ]
    assert len(first) <= cap and len(second) <= cap
    first_loader = next(
        tool for tool in first if tool.get("name") == FUNCTION_GROUP_LOADER_TOOL_NAME
    )
    assert first_loader["parameters"]["properties"]["groups"]["items"]["enum"] == (
        ["wanted", "later"]
        if recognized_delayed
        else ["wanted", "later", "immediate_only"]
    )
    response = next(
        content
        for content in log.content
        if getattr(content, "tool_call_id", None) == "load"
    )
    payload = json.loads(tool_result_data(response)["result"])
    admitted = offset <= 0
    assert payload["status"] == ("success" if admitted else "error")
    assert session.loaded_group_ids == ({"wanted"} if admitted else set())
    names = {tool.get("name", tool["type"]) for tool in second}
    assert {"web_search", CONTINUE_CONVERSATION_TOOL_NAME} <= names
    assert ("delay_0" in names) is admitted
    assert ("immediate_member" in names) is (admitted and not recognized_delayed)
    assert ("list_scheduled_actions" in names) is (
        owner and (offset == -3 or not admitted)
    )
    assert ("cancel_scheduled_action" in names) is (
        owner and not recognized_delayed and (offset == -3 or not admitted)
    )
    if admitted:
        assert payload["loadable_groups"] == (
            []
            if offset == 0
            else ["later"]
            if recognized_delayed
            else ["immediate_only", "later"]
        )
    else:
        assert first == second

    # Preview represents a request with no user text and hence no recognition.
    if not recognized_delayed:
        result = await preview.async_preview_effective_request(
            hass, wire.entry, subentry, options, "caller" if owner else ""
        )
        preview_tools = [
            tool
            for section in result["sections"]
            if section["key"]
            in {
                "function_tools",
                "function_group_loader",
                "integration_tools",
                "provider_tools",
            }
            for tool in json.loads(section["content"])
        ]
        assert {tool.get("name", tool["type"]) for tool in preview_tools} == {
            tool.get("name", tool["type"]) for tool in first
        }


def test_remaining_groups_must_fit_and_offered_loader_uses_its_reserved_slot():
    configured = [_tool(name) for name in ("first", "small", "big_one", "big_two")]
    groups = [
        _group("first", ["first"]),
        _group("small", ["small"]),
        _group("big", ["big_one", "big_two"]),
    ]
    session = FunctionGroupRuntime().begin("conversation", 30)
    admission = FunctionToolAdmission(max_tools=3, extra_tool_count=1)
    result = load_function_groups(
        session, ["first"], groups, configured, admission=admission
    )
    assert result["loadable_groups"] == ["small"]
    rejected = load_function_groups(
        session, ["big"], groups, configured, admission=admission
    )
    assert rejected["status"] == "error"
    assert session.loaded_group_ids == {"first"}

    scheduled = scheduled_action_tools()
    tools = assemble_function_tools(configured, groups, session.loaded_group_ids).tools
    admission = FunctionToolAdmission(max_tools=4, extra_tool_count=1)
    assert admission.admit([*tools, *scheduled]) == tools
    without_loader = replace(admission, loader_available=False)
    assert without_loader.admit([*tools, *scheduled]) == [configured[0], *scheduled]


def test_strict_delayed_turn_keeps_previously_loaded_immediate_group_for_later():
    immediate = _tool("immediate")
    configured = [immediate, _delay_tool("delayed")]
    groups = [_group("immediate", ["immediate"]), _group("delayed", ["delayed"])]
    session = FunctionGroupRuntime().begin("conversation", 30)
    session.loaded_group_ids.add("immediate")
    predicate = ToolSnapshot.delayed_action_tool_available
    admission = FunctionToolAdmission(request_tool_available=predicate)
    result = load_function_groups(
        session, ["delayed"], groups, configured, admission=admission
    )
    assert result["loadable_groups"] == []
    assert session.loaded_group_ids == {"delayed", "immediate"}
    strict = assemble_function_tools(
        configured, groups, session.loaded_group_ids, request_tool_available=predicate
    )
    ordinary = assemble_function_tools(configured, groups, session.loaded_group_ids)
    assert strict.tools == [configured[1]]
    assert ordinary.tools == configured
