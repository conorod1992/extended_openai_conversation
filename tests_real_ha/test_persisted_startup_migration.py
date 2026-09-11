"""Real-HA acceptance for persisted legacy entry startup and migration."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_EXPOSED_ENTITIES_ENABLED,
    CONF_MEMORY_AUTO_CREATE,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    LEGACY_CONTEXT_TRUNCATE_STRATEGY,
    MEMORY_MODE_AUTOMATIC,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _speech,
)

_LEGACY_PROMPT = "Legacy persisted startup marker."
_LEGACY_MODEL = "gpt-5.6"


def _legacy_entry() -> MockConfigEntry:
    """Return the storage shape used before conversation/AI-task subentries."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Persisted Legacy Assistant",
        data={
            CONF_API_KEY: "sk-persisted-migration-acceptance",
            CONF_SKIP_AUTHENTICATION: True,
        },
        options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: _LEGACY_MODEL,
            CONF_PROMPT: _LEGACY_PROMPT,
            # Exercise the compatibility conversion from the historical booleans to
            # the modern canonical memory mode rather than seeding the new field.
            CONF_MEMORY_ENABLED: True,
            CONF_MEMORY_AUTO_CREATE: True,
        },
        version=1,
    )


def _conversation_subentry(entry: MockConfigEntry) -> Any:
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


async def _say(hass: HomeAssistant, agent: Any, text: str):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _assert_migrated_entry(entry: MockConfigEntry, subentry_id: str | None = None) -> str:
    """Require the persisted legacy shape to be fully modernized."""
    assert entry.version == CONFIG_ENTRY_VERSION
    assert dict(entry.options) == {}
    assert {item.subentry_type for item in entry.subentries.values()} == {
        "conversation",
        "ai_task_data",
    }

    subentry = _conversation_subentry(entry)
    if subentry_id is not None:
        assert subentry.subentry_id == subentry_id
    data = dict(subentry.data)
    assert data[CONF_API_MODE] == API_MODE_CHAT_COMPLETIONS
    assert data[CONF_CHAT_MODEL] == _LEGACY_MODEL
    assert data[CONF_PROMPT] == _LEGACY_PROMPT
    assert data[CONF_MEMORY_MODE] == MEMORY_MODE_AUTOMATIC
    assert data[CONF_MEMORY_ENABLED] is True
    assert data[CONF_MEMORY_AUTO_CREATE] is True
    # These two defaults are deliberately compatibility values for migrated users,
    # not the defaults applied to a newly-created modern agent.
    assert data[CONF_CONTEXT_TRUNCATE_STRATEGY] == LEGACY_CONTEXT_TRUNCATE_STRATEGY
    assert data[CONF_EXPOSED_ENTITIES_ENABLED] is False
    return subentry.subentry_id


async def test_persisted_v1_entry_migrates_before_agent_start_and_survives_reload(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """Cold startup must migrate persisted data before exposing a usable agent."""
    entry = _legacy_entry()
    entry.add_to_hass(hass)

    # Enter only through HA's config-entry lifecycle. Integration async_setup performs
    # the migration before platform forwarding; the test does not call migration code.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    subentry_id = _assert_migrated_entry(entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)

    # Join persistence/migration to the real runtime boundary: the values moved out of
    # legacy entry.options must be the values serialized by the real OpenAI SDK.
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Migrated startup works.")])
    result = await _say(hass, agent, "Check the migrated assistant")
    assert _speech(result) == "Migrated startup works."
    assert [request["path"] for request in wire.requests] == ["/v1/chat/completions"]
    first_body = wire.requests[0]["body"]
    assert first_body["model"] == _LEGACY_MODEL
    system_text = "\n".join(
        item.get("content", "")
        for item in first_body["messages"]
        if item.get("role") == "system"
    )
    assert _LEGACY_PROMPT in system_text

    # Cross a genuine unload/setup boundary. The migrated subentry itself—not an
    # in-process compatibility view—must now be durable and load identically.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    _assert_migrated_entry(entry, subentry_id)
    reloaded_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(reloaded_agent, ExtendedOpenAIAgentEntity)
    assert reloaded_agent is not agent

    reloaded_wire = _install_wire(
        monkeypatch,
        reloaded_agent,
        [_chat_sse_text("Persisted migration works after reload.")],
    )
    reloaded = await _say(hass, reloaded_agent, "Check the persisted migrated assistant")
    assert _speech(reloaded) == "Persisted migration works after reload."
    assert reloaded_wire.requests[0]["body"]["model"] == _LEGACY_MODEL
    assert _LEGACY_PROMPT in "\n".join(
        item.get("content", "")
        for item in reloaded_wire.requests[0]["body"]["messages"]
        if item.get("role") == "system"
    )
