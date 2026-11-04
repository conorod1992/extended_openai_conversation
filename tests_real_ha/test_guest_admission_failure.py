"""Native HA live admissions must not pollute another continuity scope on failure."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as runtime,
)
from custom_components.extended_openai_conversation_responses.ha_chat_session_compat import (
    async_get_chat_session,
)
from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import (
    DATA_CHAT_LOGS,
    async_get_chat_log,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_provider_wire_e2e import (
    _install_wire,
    _chat_sse_text,
    _responses_sse_text,
)


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"),
    reason="Requires live HA ChatLog admissions",
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("guest_active", [False, True])
@pytest.mark.parametrize("failure", [HomeAssistantError, asyncio.CancelledError])
@pytest.mark.parametrize("stage", ["before_continuity", "after_continuity"])
async def test_guest_failure_never_retains_admitted_input_in_owner_history(
    hass, monkeypatch, failure, stage, guest_active, api
):
    user = await hass.auth.async_create_user(
        "Admission owner", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    agent = await _agent(hass, conversation_continuity="user", api_mode=api)
    text_reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(
        monkeypatch,
        agent,
        [text_reply("Owner response"), text_reply("Owner resumed")],
    )

    async def say(text, conversation_id):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=context,
            language="en",
            agent_id=agent.entity_id,
        )

    owner = await say("Original owner input", None)
    assert owner.response.error_code is None
    owner_id = owner.conversation_id
    if guest_active:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    admitted = conversation.ConversationInput(
        text="GUEST_ADMISSION_SENTINEL",
        device_id=None,
        satellite_id=None,
        context=context,
        language="en",
        conversation_id=owner_id,
        agent_id=agent.entity_id,
    )
    with monkeypatch.context() as patch:
        if stage == "before_continuity":
            patch.setattr(
                runtime,
                "async_prepare_runtime_function_tools",
                AsyncMock(side_effect=failure("admission interrupted")),
            )
        else:
            patch.setattr(
                agent,
                "_async_begin_archive_session",
                AsyncMock(side_effect=failure("admission interrupted")),
            )
        with pytest.raises(failure):
            with async_get_chat_session(
                hass, owner_id, context=context
            ) as source_session:
                with async_get_chat_log(hass, source_session, admitted):
                    await agent.async_process(admitted)
    assert len(wire.requests) == 1
    source = hass.data[DATA_CHAT_LOGS][owner_id]
    owned_failure = not guest_active and stage == "after_continuity"
    assert [item.content for item in source.content if item.role == "user"] == (
        ["Original owner input", "GUEST_ADMISSION_SENTINEL"]
        if owned_failure
        else ["Original owner input"]
    )
    await agent._guest_mode.async_disable_trusted()
    resumed = await say("Owner follow-up", owner_id)
    assert resumed.response.error_code is None
    assert (
        "GUEST_ADMISSION_SENTINEL" in json.dumps(wire.requests[-1]["body"])
    ) == owned_failure


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"),
    reason="Requires live HA ChatLog admissions",
)
@pytest.mark.parametrize("guest_active", [False, True])
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("failure", [HomeAssistantError, asyncio.CancelledError])
@pytest.mark.parametrize("stage", ["before_continuity", "after_continuity"])
async def test_failed_delegation_preserves_foreign_parent_input(
    hass, monkeypatch, guest_active, api, failure, stage
):
    agent = await _agent(hass, conversation_continuity="user", api_mode=api)
    user = await hass.auth.async_create_user(
        "Delegating owner", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    if guest_active:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    parent_input = conversation.ConversationInput(
        text="Delegate this same question",
        device_id=None,
        satellite_id=None,
        context=context,
        language="en",
        conversation_id="foreign-parent",
        agent_id="conversation.other_integration",
    )
    if stage == "before_continuity":
        monkeypatch.setattr(
            runtime,
            "async_prepare_runtime_function_tools",
            AsyncMock(side_effect=failure("child unavailable")),
        )
    else:
        monkeypatch.setattr(
            agent,
            "_async_begin_archive_session",
            AsyncMock(side_effect=failure("child unavailable")),
        )
    with async_get_chat_session(hass, "foreign-parent", context=context) as session:
        with async_get_chat_log(hass, session, parent_input) as parent:

            async def delegate():
                return await conversation.async_converse(
                    hass=hass,
                    text=parent_input.text,
                    conversation_id=session.conversation_id,
                    context=context,
                    language="en",
                    agent_id=agent.entity_id,
                )

            if failure is asyncio.CancelledError:
                with pytest.raises(asyncio.CancelledError):
                    await delegate()
            else:
                result = await delegate()
                assert result.response.error_code is not None
            assert [item.content for item in parent.content if item.role == "user"] == [
                parent_input.text
            ]
            parent.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=parent_input.agent_id,
                    content="Parent handled the failed delegation",
                )
            )
    stored = hass.data[DATA_CHAT_LOGS][session.conversation_id]
    assert [item.content for item in stored.content if item.role == "user"] == [
        parent_input.text
    ]


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"),
    reason="Requires live HA ChatLog admissions",
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_first_unclaimed_guest_log_is_never_later_owner_context(
    hass, monkeypatch, api, outcome
):
    agent = await _agent(hass, conversation_continuity="ha_default", api_mode=api)
    user = await hass.auth.async_create_user("First owner", group_ids=["system-admin"])
    context = Context(user_id=user.id)
    reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(
        monkeypatch,
        agent,
        [reply("Guest response"), reply("Owner response")]
        if outcome == "success"
        else [reply("Owner response")],
    )
    await agent._guest_mode.async_update_trusted(indefinite=True)
    admitted = conversation.ConversationInput(
        text="UNCLAIMED_GUEST_SENTINEL",
        device_id=None,
        satellite_id=None,
        context=context,
        language="en",
        conversation_id="first-unclaimed",
        agent_id=agent.entity_id,
    )
    with monkeypatch.context() as patch:
        if outcome != "success":
            failure = (
                asyncio.CancelledError if outcome == "cancel" else HomeAssistantError
            )
            patch.setattr(
                runtime,
                "async_prepare_runtime_function_tools",
                AsyncMock(side_effect=failure("not ready")),
            )
        with async_get_chat_session(
            hass, "first-unclaimed", context=context
        ) as session:
            with async_get_chat_log(hass, session, admitted) as source:
                if outcome == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        await agent.async_process(admitted)
                else:
                    result = await conversation.async_converse(
                        hass=hass,
                        text=admitted.text,
                        conversation_id=session.conversation_id,
                        context=context,
                        language="en",
                        agent_id=agent.entity_id,
                    )
                    assert (result.response.error_code is None) == (
                        outcome == "success"
                    )
                assert [
                    item.content for item in source.content if item.role == "user"
                ] == ["UNCLAIMED_GUEST_SENTINEL"]
    await agent._guest_mode.async_disable_trusted()
    resumed = await conversation.async_converse(
        hass=hass,
        text="Owner follow-up",
        conversation_id="first-unclaimed",
        context=context,
        language="en",
        agent_id=agent.entity_id,
    )
    assert resumed.response.error_code is None
    assert resumed.conversation_id != "first-unclaimed"
    assert "UNCLAIMED_GUEST_SENTINEL" not in json.dumps(wire.requests[-1]["body"])


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"),
    reason="Requires live HA ChatLog admissions",
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("fail_owned", [False, True])
async def test_initial_ha_default_parent_binds_an_owned_log_and_retains_failed_context(
    hass, monkeypatch, api, fail_owned
):
    agent = await _agent(hass, conversation_continuity="ha_default", api_mode=api)
    user = await hass.auth.async_create_user(
        "Initial owner", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(
        monkeypatch,
        agent,
        [reply("First response"), reply("Follow-up")]
        if not fail_owned
        else [reply("Follow-up")],
    )
    admitted = conversation.ConversationInput(
        text="INITIAL_OWNER_SENTINEL",
        device_id=None,
        satellite_id=None,
        context=context,
        language="en",
        conversation_id="initial-owner",
        agent_id=agent.entity_id,
    )
    with monkeypatch.context() as patch:
        if fail_owned:
            patch.setattr(
                agent,
                "_async_begin_archive_session",
                AsyncMock(side_effect=HomeAssistantError("owned failure")),
            )
        with async_get_chat_session(hass, "initial-owner", context=context) as session:
            with async_get_chat_log(hass, session, admitted) as source:
                result = await conversation.async_converse(
                    hass=hass,
                    text=admitted.text,
                    conversation_id=session.conversation_id,
                    context=context,
                    language="en",
                    agent_id=agent.entity_id,
                )
                assert (result.response.error_code is None) == (not fail_owned)
                owned_ids = set(hass.data[DATA_CHAT_LOGS]) - {"initial-owner"}
                assert len(owned_ids) == 1
                canonical_id = owned_ids.pop()
                assert result.conversation_id == (
                    "initial-owner" if fail_owned else canonical_id
                )
                assert [
                    item.content for item in source.content if item.role == "user"
                ] == ["INITIAL_OWNER_SENTINEL"]
    resumed = await conversation.async_converse(
        hass=hass,
        text="Owner follow-up",
        conversation_id="initial-owner",
        context=context,
        language="en",
        agent_id=agent.entity_id,
    )
    assert resumed.response.error_code is None
    assert resumed.conversation_id == canonical_id
    assert canonical_id != "initial-owner"
    assert "INITIAL_OWNER_SENTINEL" in json.dumps(wire.requests[-1]["body"])


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"),
    reason="Requires live HA ChatLog admissions",
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("guest_second", [False, True])
@pytest.mark.parametrize("forward_text", ["same", "different"])
async def test_successful_delegate_never_claims_the_parent_history(
    hass, monkeypatch, api, guest_second, forward_text
):
    agent = await _agent(hass, conversation_continuity="ha_default", api_mode=api)
    user = await hass.auth.async_create_user(
        "Repeat parent", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(monkeypatch, agent, [reply("Child answer")])

    async def delegate(text):
        return await conversation.async_converse(
            hass=hass,
            text=text if forward_text == "same" else "Delegate: " + text,
            conversation_id="repeated-parent",
            context=context,
            language="en",
            agent_id=agent.entity_id,
        )

    def parent_input(text):
        return conversation.ConversationInput(
            text=text,
            device_id=None,
            satellite_id=None,
            context=context,
            language="en",
            conversation_id="repeated-parent",
            agent_id="conversation.router",
        )

    with async_get_chat_session(hass, "repeated-parent", context=context) as session:
        with async_get_chat_log(
            hass, session, parent_input("First delegated question")
        ) as source:
            result = await delegate("First delegated question")
            assert result.response.error_code is None
            source.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id="conversation.router",
                    content="Parent completed first turn",
                )
            )
    if guest_second:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    monkeypatch.setattr(
        runtime,
        "async_prepare_runtime_function_tools",
        AsyncMock(side_effect=HomeAssistantError("Second delegate unavailable")),
    )
    with async_get_chat_session(hass, "repeated-parent", context=context) as session:
        with async_get_chat_log(
            hass, session, parent_input("Second delegated question")
        ) as source:
            result = await delegate("Second delegated question")
            assert result.response.error_code is not None
            assert [item.content for item in source.content if item.role == "user"] == [
                "First delegated question",
                "Second delegated question",
            ]
            source.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id="conversation.router",
                    content="Parent recovered second turn",
                )
            )
    assert len(wire.requests) == 1


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"), reason="Requires live HA logs"
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
async def test_parent_delegations_keep_owner_and_guest_contexts_separate(
    hass, monkeypatch, api
):
    agent = await _agent(hass, conversation_continuity="ha_default", api_mode=api)
    user = await hass.auth.async_create_user("Bound owner", group_ids=["system-admin"])
    context = Context(user_id=user.id)
    reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(monkeypatch, agent, [reply("Acknowledged") for _ in range(4)])
    ids = []
    with async_get_chat_session(hass, "aba-parent", context=context) as session:
        original = conversation.ConversationInput(
            text="FOREIGN_PRIVATE_SENTINEL",
            device_id=None,
            satellite_id=None,
            context=context,
            language="en",
            conversation_id=session.conversation_id,
            agent_id="conversation.router",
        )
        with async_get_chat_log(hass, session, original) as source:
            source.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id="conversation.router", content="Foreign answer"
                )
            )
    for index, (guest, text) in enumerate(
        [
            (False, "OWNER_CHILD_SENTINEL"),
            (True, "GUEST_CHILD_SENTINEL"),
            (True, "Guest follow-up"),
            (False, "Owner follow-up"),
        ]
    ):
        if guest:
            await agent._guest_mode.async_update_trusted(indefinite=True)
        else:
            await agent._guest_mode.async_disable_trusted()
        supplied = conversation.ConversationInput(
            text=text,
            device_id=None,
            satellite_id=None,
            context=context,
            language="en",
            conversation_id="aba-parent",
            agent_id="conversation.router",
        )
        with async_get_chat_session(hass, "aba-parent", context=context) as session:
            with async_get_chat_log(hass, session, supplied) as source:
                result = await conversation.async_converse(
                    hass=hass,
                    text=text,
                    conversation_id="aba-parent",
                    context=context,
                    language="en",
                    agent_id=agent.entity_id,
                )
                assert result.response.error_code is None
                ids.append(result.conversation_id)
                source.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(
                        agent_id="conversation.router", content="Parent answer"
                    )
                )
        sent = json.dumps(wire.requests[index]["body"])
        assert "FOREIGN_PRIVATE_SENTINEL" not in sent
        if guest:
            assert "OWNER_CHILD_SENTINEL" not in sent
        else:
            assert "GUEST_CHILD_SENTINEL" not in sent
        if index == 2:
            assert "GUEST_CHILD_SENTINEL" in sent
        if index == 3:
            assert "OWNER_CHILD_SENTINEL" in sent
    assert ids[0] == ids[3] and ids[1] == ids[2] and ids[0] != ids[1]
    assert "aba-parent" not in ids


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"), reason="Requires live HA logs"
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("guest", [False, True])
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_parent_alias_preserves_completed_effect_replay_guard(
    hass, monkeypatch, api, guest, failure
):
    from copy import deepcopy
    from custom_components.extended_openai_conversation_responses.const import (
        DEFAULT_CONF_FUNCTION_TOOLS,
    )
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from homeassistant.setup import async_setup_component
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_tool_call,
        _responses_sse_tool_call,
        _raw_client,
    )

    agent = await _agent(
        hass,
        conversation_continuity="ha_default",
        api_mode=api,
        functions=[deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])],
        guest_policy_version=2,
        guest_function_policy="on",
    )
    user = await hass.auth.async_create_user(
        "Replay parent", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    assert await async_setup_component(
        hass,
        "counter",
        {"counter": {"parent_replay_effect": {"initial": 0, "step": 1}}},
    )
    await hass.async_block_till_done()
    entity_id = "counter.parent_replay_effect"
    async_expose_entity(hass, "conversation", entity_id, True)
    if guest:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    arguments = {
        "list": [
            {
                "domain": "counter",
                "service": "increment",
                "service_data": {"entity_id": [entity_id]},
            }
        ]
    }
    tool = (
        _chat_sse_tool_call if api == "chat_completions" else _responses_sse_tool_call
    )
    text = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            tool(call_id="first-parent-call", arguments=arguments)
            if api == "chat_completions"
            else tool(call_id="first-parent-call", tool_arguments=arguments),
            (500, {"error": {"message": "Continuation lost", "type": "server_error"}}),
            tool(call_id="second-parent-call", arguments=arguments)
            if api == "chat_completions"
            else tool(call_id="second-parent-call", tool_arguments=arguments),
            text("Already handled"),
            tool(call_id="intentional-parent-repeat", arguments=arguments)
            if api == "chat_completions"
            else tool(call_id="intentional-parent-repeat", tool_arguments=arguments),
            text("Intentionally repeated after acknowledgement"),
        ],
    )
    monkeypatch.setattr(_raw_client(agent), "max_retries", 0)
    send = wire.send

    async def transport(request, *args, **kwargs):
        if failure == "cancel" and len(wire.requests) == 1:
            assert hass.states.get(entity_id).state == "1"
            wire.requests.append(
                {"path": request.url.path, "body": json.loads(request.content)}
            )
            raise asyncio.CancelledError()
        return await send(request, *args, **kwargs)

    monkeypatch.setattr(_raw_client(agent)._client, "send", transport)
    canonical = None
    cleanup_count = None
    for attempt in range(4):
        supplied = conversation.ConversationInput(
            text="Increment once",
            device_id=None,
            satellite_id=None,
            context=context,
            language="en",
            conversation_id="replay-parent",
            agent_id="conversation.router",
        )
        with async_get_chat_session(hass, "replay-parent", context=context) as session:
            with async_get_chat_log(hass, session, supplied) as source:

                async def delegate():
                    return await conversation.async_converse(
                        hass=hass,
                        text=supplied.text,
                        conversation_id="replay-parent",
                        context=context,
                        language="en",
                        agent_id=agent.entity_id,
                    )

                if attempt == 0 and failure == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        await delegate()
                else:
                    result = await delegate()
                    if attempt in (0, 1):
                        assert result.response.error_code is not None
                    else:
                        assert result.response.error_code is None
                        assert result.conversation_id == canonical
                actual_ids = set(hass.data[DATA_CHAT_LOGS]) - {"replay-parent"}
                assert len(actual_ids) == 1
                current = actual_ids.pop()
                if canonical is None:
                    canonical = current
                assert current == canonical
                from homeassistant.helpers.chat_session import DATA_CHAT_SESSION

                count = len(hass.data[DATA_CHAT_SESSION][canonical]._cleanup_callbacks)
                if cleanup_count is None:
                    cleanup_count = count
                assert count == cleanup_count
                source.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(
                        agent_id="conversation.router", content="Parent recorded result"
                    )
                )
        assert hass.states.get(entity_id).state == ("1" if attempt < 3 else "2")
    assert len(wire.requests) == 6
    assert canonical != "replay-parent"


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"), reason="Requires live HA logs"
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("guest", [False, True])
@pytest.mark.parametrize("transition", ["reset", "expire_parent", "expire_child"])
async def test_parent_binding_reset_and_native_expiry_do_not_resurrect_history(
    hass, monkeypatch, freezer, api, guest, transition
):
    from datetime import timedelta
    from homeassistant.helpers.chat_session import (
        DATA_CHAT_SESSION,
        DATA_CHAT_SESSION_CLEANUP,
    )
    from homeassistant.util import dt as dt_util

    agent = await _agent(hass, conversation_continuity="ha_default", api_mode=api)
    user = await hass.auth.async_create_user(
        "Lifecycle parent", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    if guest:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(monkeypatch, agent, [reply("Acknowledged") for _ in range(4)])

    def supplied(text):
        return conversation.ConversationInput(
            text=text,
            device_id=None,
            satellite_id=None,
            context=context,
            language="en",
            conversation_id="lifecycle-parent",
            agent_id="conversation.router",
        )

    async def say(text, incoming):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=incoming,
            context=context,
            language="en",
            agent_id=agent.entity_id,
        )

    async def delegated(text):
        with async_get_chat_session(
            hass, "lifecycle-parent", context=context
        ) as session:
            with async_get_chat_log(hass, session, supplied(text)) as source:
                result = await say(text, "lifecycle-parent")
                assert result.response.error_code is None
                source.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(
                        agent_id="conversation.router", content="Parent answer"
                    )
                )
                return result, source

    start = dt_util.utcnow()
    original, original_source = await delegated("OLD_CHILD_SENTINEL")
    if transition == "reset":
        await agent._continuity.async_ignore_next_incoming_conversation_id(
            original.conversation_id
        )
    else:
        freezer.move_to(start + timedelta(seconds=240))
        if transition == "expire_parent":
            kept = await say("Keep child active", original.conversation_id)
            assert kept.response.error_code is None
        else:
            with async_get_chat_session(
                hass, "lifecycle-parent", context=context
            ) as session:
                with async_get_chat_log(
                    hass, session, supplied("Parent-only turn")
                ) as source:
                    source.async_add_assistant_content_without_tools(
                        conversation.AssistantContent(
                            agent_id="conversation.router", content="Parent-only answer"
                        )
                    )
        freezer.move_to(start + timedelta(seconds=302))
        cleanup = hass.data[DATA_CHAT_SESSION_CLEANUP]
        if cleanup.unsub:
            cleanup.unsub()
            cleanup.unsub = None
        cleanup._cleanup(dt_util.utcnow())
        assert ("lifecycle-parent" in hass.data[DATA_CHAT_SESSION]) == (
            transition == "expire_child"
        )
        assert (original.conversation_id in hass.data[DATA_CHAT_SESSION]) == (
            transition == "expire_parent"
        )
    fresh, current_source = await delegated("FRESH_CHILD_SENTINEL")
    assert fresh.conversation_id != original.conversation_id
    assert "OLD_CHILD_SENTINEL" not in json.dumps(wire.requests[-1]["body"])
    if transition == "expire_parent":
        assert current_source is not original_source
    else:
        assert current_source is original_source
    repeated, _source = await delegated("Fresh follow-up")
    assert repeated.conversation_id == fresh.conversation_id
    sent = json.dumps(wire.requests[-1]["body"])
    assert "FRESH_CHILD_SENTINEL" in sent and "OLD_CHILD_SENTINEL" not in sent


@pytest.mark.skipif(
    not hasattr(conversation.ChatLog, "turn_active"), reason="Requires live HA logs"
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
async def test_explicit_child_ids_do_not_rebind_the_foreign_parent(
    hass, monkeypatch, api
):
    agent = await _agent(hass, conversation_continuity="ha_default", api_mode=api)
    user = await hass.auth.async_create_user(
        "Explicit parent", group_ids=["system-admin"]
    )
    context = Context(user_id=user.id)
    reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(monkeypatch, agent, [reply("Child answer") for _ in range(3)])
    ids = []
    for incoming, text in [
        ("explicit-child-one", "CHILD_ONE_SENTINEL"),
        ("explicit-child-two", "CHILD_TWO_SENTINEL"),
        ("explicit-parent", "Parent delegation"),
    ]:
        supplied = conversation.ConversationInput(
            text="Parent: " + text,
            device_id=None,
            satellite_id=None,
            context=context,
            language="en",
            conversation_id="explicit-parent",
            agent_id="conversation.router",
        )
        with async_get_chat_session(
            hass, "explicit-parent", context=context
        ) as session:
            with async_get_chat_log(hass, session, supplied) as source:
                result = await conversation.async_converse(
                    hass=hass,
                    text=text,
                    conversation_id=incoming,
                    context=context,
                    language="en",
                    agent_id=agent.entity_id,
                )
                assert result.response.error_code is None
                ids.append(result.conversation_id)
                source.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(
                        agent_id="conversation.router", content="Parent answer"
                    )
                )
    assert ids[:2] == ["explicit-child-one", "explicit-child-two"]
    assert len(set(ids)) == 3
    sent = json.dumps(wire.requests[-1]["body"])
    assert "CHILD_ONE_SENTINEL" not in sent and "CHILD_TWO_SENTINEL" not in sent
