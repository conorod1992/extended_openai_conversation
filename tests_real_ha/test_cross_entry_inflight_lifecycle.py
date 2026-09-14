"""Real-HA acceptance for cross-entry lifecycle isolation during an active request."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _speech,
)

_WAIT_TIMEOUT = 10
_MODEL_A = "gpt-5.6"
_MODEL_B = "gpt-4.1-mini"


def _agent(hass: HomeAssistant, entry) -> ExtendedOpenAIAgentEntity:
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)
    return agent


def _current_text(log: conversation.ChatLog) -> str:
    return next(
        item.content
        for item in reversed(log.content)
        if isinstance(getattr(item, "content", None), str)
    )


async def _converse(
    hass: HomeAssistant,
    entry,
    text: str,
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )


@pytest.mark.asyncio
async def test_other_entry_reload_does_not_disturb_inflight_request(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reloading entry B must not replace or disrupt entry A mid-request."""
    entry_a = _make_entry(
        "Cross-entry A",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: _MODEL_A,
        },
    )
    entry_b = _make_entry(
        "Cross-entry B",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: _MODEL_B,
        },
    )
    await _setup_entry(hass, entry_a)
    await _setup_entry(hass, entry_b)

    agent_a = _agent(hass, entry_a)
    agent_b_before = _agent(hass, entry_b)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_model(log: conversation.ChatLog, **kwargs: Any) -> None:
        del kwargs
        current = _current_text(log)
        entered.set()
        await release.wait()
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent_a.entity_id,
                content=f"entry-a:{current}",
            )
        )

    monkeypatch.setattr(agent_a, "_async_handle_chat_log", blocking_model)

    request_a = asyncio.create_task(
        _converse(hass, entry_a, "request crossing entry-b reload")
    )
    await asyncio.wait_for(entered.wait(), timeout=_WAIT_TIMEOUT)
    assert not request_a.done()
    assert _agent(hass, entry_a) is agent_a

    # Exercise Home Assistant's real unload/setup lifecycle for a completely
    # separate ExtendedOpenAI config entry while A's request is still awaiting.
    assert await hass.config_entries.async_unload(entry_b.entry_id)
    await asyncio.wait_for(hass.async_block_till_done(), timeout=_WAIT_TIMEOUT)
    assert entry_b.state is ConfigEntryState.NOT_LOADED
    assert conversation.async_get_agent(hass, entry_b.entry_id) is None

    # Entry A must remain fully registered and its in-flight task must remain
    # pending rather than being cancelled or rebound during B's teardown.
    assert entry_a.state is ConfigEntryState.LOADED
    assert _agent(hass, entry_a) is agent_a
    assert not request_a.done()

    assert await hass.config_entries.async_setup(entry_b.entry_id)
    await asyncio.wait_for(hass.async_block_till_done(), timeout=_WAIT_TIMEOUT)
    assert entry_b.state is ConfigEntryState.LOADED
    agent_b_after = _agent(hass, entry_b)
    assert agent_b_after is not agent_b_before
    assert _agent(hass, entry_a) is agent_a
    assert not request_a.done()

    # Prove the replacement B runtime is genuinely healthy and still consumes
    # only B's configuration while A remains blocked.
    wire_b = _install_wire(
        monkeypatch,
        agent_b_after,
        [_chat_sse_text("entry B fresh after reload")],
    )
    fresh_b = await _converse(hass, entry_b, "fresh request on entry B")
    assert _speech(fresh_b) == "entry B fresh after reload"
    assert len(wire_b.requests) == 1
    assert wire_b.requests[0]["path"] == "/v1/chat/completions"
    assert wire_b.requests[0]["body"]["model"] == _MODEL_B
    assert _agent(hass, entry_a) is agent_a
    assert not request_a.done()

    # A's old request should then complete normally on its original runtime and
    # must not have been replaced, unregistered, or contaminated by B's reload.
    release.set()
    result_a = await asyncio.wait_for(request_a, timeout=_WAIT_TIMEOUT)
    assert _speech(result_a) == "entry-a:request crossing entry-b reload"
    assert _agent(hass, entry_a) is agent_a
    assert _agent(hass, entry_b) is agent_b_after

    # Finally prove A still accepts a subsequent request after the cross-entry
    # lifecycle churn, rather than merely allowing the pre-existing call to end.
    wire_a = _install_wire(
        monkeypatch,
        agent_a,
        [_chat_sse_text("entry A remains healthy")],
    )
    fresh_a = await _converse(hass, entry_a, "fresh request on entry A")
    assert _speech(fresh_a) == "entry A remains healthy"
    assert len(wire_a.requests) == 1
    assert wire_a.requests[0]["path"] == "/v1/chat/completions"
    assert wire_a.requests[0]["body"]["model"] == _MODEL_A
