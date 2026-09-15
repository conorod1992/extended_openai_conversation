"""Real-HA acceptance for sibling subentry reload during an in-flight request."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import CONF_CHAT_MODEL
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er

from tests_real_ha.test_acceptance_lifecycle import _setup_entry
from tests_real_ha.test_cross_feature_acceptance import _provider, _speech
from tests_real_ha.test_subentry_runtime_policy_isolation import (
    _A_TITLE,
    _B_TITLE,
    _USER_ID,
    _agent_by_title,
    _entry,
)

_WAIT_TIMEOUT = 10
_OLD_MODEL = "gpt-5.6"
_NEW_B_MODEL = "gpt-4.1-mini"


async def _say(
    hass: HomeAssistant,
    agent: Any,
    text: str,
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=_USER_ID),
        language="en",
        agent_id=agent.entity_id,
    )


def _current_text(log: conversation.ChatLog) -> str:
    return next(
        item.content
        for item in reversed(log.content)
        if isinstance(getattr(item, "content", None), str)
    )


@pytest.mark.asyncio
async def test_sibling_subentry_reload_does_not_poison_inflight_request(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing sibling B must not corrupt or resurrect in-flight sibling A."""
    MockUser(id=_USER_ID, name="Sibling Reload Owner", is_owner=True).add_to_hass(hass)
    entry = _entry()
    await _setup_entry(hass, entry)

    subentry_a, old_agent_a = _agent_by_title(hass, entry, _A_TITLE)
    subentry_b, old_agent_b = _agent_by_title(hass, entry, _B_TITLE)
    assert subentry_a.data[CONF_CHAT_MODEL] == _OLD_MODEL
    assert subentry_b.data[CONF_CHAT_MODEL] == _OLD_MODEL

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_model(log: conversation.ChatLog, **kwargs: Any) -> None:
        del kwargs
        current = _current_text(log)
        entered.set()
        await release.wait()
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=old_agent_a.entity_id,
                content=f"old-a-generation:{current}",
            )
        )

    monkeypatch.setattr(old_agent_a, "_async_handle_chat_log", blocking_model)

    old_request_a = asyncio.create_task(
        _say(hass, old_agent_a, "request crossing sibling B reload")
    )
    await asyncio.wait_for(entered.wait(), timeout=_WAIT_TIMEOUT)
    assert not old_request_a.done()

    updated_b = dict(subentry_b.data)
    updated_b[CONF_CHAT_MODEL] = _NEW_B_MODEL
    hass.config_entries.async_update_subentry(entry, subentry_b, data=updated_b)

    # Updating B triggers the integration's real parent-entry reload. A's old request
    # remains alive on its captured generation while fresh A and B runtimes replace it.
    await asyncio.wait_for(hass.async_block_till_done(), timeout=_WAIT_TIMEOUT)
    assert entry.state is ConfigEntryState.LOADED
    assert not old_request_a.done()

    fresh_subentry_a, fresh_agent_a = _agent_by_title(hass, entry, _A_TITLE)
    fresh_subentry_b, fresh_agent_b = _agent_by_title(hass, entry, _B_TITLE)
    assert fresh_agent_a is not old_agent_a
    assert fresh_agent_b is not old_agent_b
    assert fresh_subentry_a.subentry_id == subentry_a.subentry_id
    assert fresh_subentry_b.subentry_id == subentry_b.subentry_id
    assert fresh_subentry_a.data[CONF_CHAT_MODEL] == _OLD_MODEL
    assert fresh_subentry_b.data[CONF_CHAT_MODEL] == _NEW_B_MODEL
    assert fresh_agent_a.subentry.data[CONF_CHAT_MODEL] == _OLD_MODEL
    assert fresh_agent_b.subentry.data[CONF_CHAT_MODEL] == _NEW_B_MODEL

    sent_a = _provider(monkeypatch, fresh_agent_a, ["fresh A remained isolated"])
    result_a = await _say(hass, fresh_agent_a, "fresh request to A")
    assert _speech(result_a) == "fresh A remained isolated"
    assert len(sent_a) == 1
    assert sent_a[0]["model"] == _OLD_MODEL

    sent_b = _provider(monkeypatch, fresh_agent_b, ["fresh B uses updated config"])
    result_b = await _say(hass, fresh_agent_b, "fresh request to B")
    assert _speech(result_b) == "fresh B uses updated config"
    assert len(sent_b) == 1
    assert sent_b[0]["model"] == _NEW_B_MODEL
    assert not old_request_a.done()

    # The stale A generation may finish, but it must never re-register itself or
    # overwrite either of the fresh sibling entities installed by B's reload.
    release.set()
    old_result = await asyncio.wait_for(old_request_a, timeout=_WAIT_TIMEOUT)
    assert _speech(old_result) == "old-a-generation:request crossing sibling B reload"
    assert conversation.async_get_agent(hass, fresh_agent_a.entity_id) is fresh_agent_a
    assert conversation.async_get_agent(hass, fresh_agent_b.entity_id) is fresh_agent_b

    rows = [
        row
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.domain == conversation.DOMAIN
    ]
    assert len(rows) == 2
    assert {row.config_subentry_id for row in rows} == {
        fresh_subentry_a.subentry_id,
        fresh_subentry_b.subentry_id,
    }
