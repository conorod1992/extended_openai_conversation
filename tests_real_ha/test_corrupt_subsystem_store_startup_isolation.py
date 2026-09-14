"""Real-HA startup isolation when one persisted subsystem store is corrupt."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_KNOWLEDGE_ENABLED,
    CONF_TEMPORARY_MEMORY,
    CONVERSATION_CONTINUITY_USER,
    SUBSYSTEM_STATUS_KEY,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    STORAGE_KEY_PREFIX as KNOWLEDGE_STORAGE_KEY_PREFIX,
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockUser
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import _chat_sse_text
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech

_USER_ID = "corrupt-store-user"
_SCOPE_ID = f"user:{_USER_ID}"
_TEMPORARY_MARKER = "Sibling temporary memory survives the corrupt knowledge store."
_KNOWLEDGE_MARKER = "knowledge-store-before-corruption"
_MANAGER_TYPES = (KnowledgeLibrary, TemporaryMemory, UsageManager)


def _system_prompt(request_body: dict[str, Any]) -> str:
    """Return the system prompt from one serialized Chat Completions request."""
    message = next(
        item for item in request_body["messages"] if item.get("role") == "system"
    )
    return str(message["content"])


def _provider_tool_names(request_body: dict[str, Any]) -> set[str]:
    """Return function-tool names exposed to the provider."""
    return {
        str(tool["function"]["name"])
        for tool in request_body.get("tools", [])
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict)
    }


def _purge_cached_managers(value: Any) -> None:
    """Remove cached subsystem managers recursively from Home Assistant data."""
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, _MANAGER_TYPES):
                del value[key]
            else:
                _purge_cached_managers(item)
    elif isinstance(value, list):
        for item in value:
            _purge_cached_managers(item)


def _contains_cached_manager(value: Any) -> bool:
    """Return whether a purged subsystem-manager instance remains nested here."""
    if isinstance(value, _MANAGER_TYPES):
        return True
    if isinstance(value, dict):
        return any(_contains_cached_manager(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_cached_manager(item) for item in value)
    return False


@pytest.mark.asyncio
async def test_corrupt_knowledge_store_on_fresh_setup_does_not_poison_siblings(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One unreadable real Store degrades Knowledge while siblings cold-start."""
    MockUser(id=_USER_ID, name="Corrupt Store User").add_to_hass(hass)

    entry = _make_entry(
        "Corrupt Store Isolation",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    await _setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._knowledge is not None
    assert agent._temporary_memory is not None
    assert agent._usage is not None

    subentry_id = agent.subentry.subentry_id
    old_knowledge = agent._knowledge
    old_temporary = agent._temporary_memory
    old_usage = agent._usage

    # Persist genuine state in the subsystem we will later corrupt and in a sibling
    # subsystem that must survive the same unload/cold-start boundary.
    await old_knowledge.async_create(
        "Pre-corruption knowledge",
        "Valid source persisted before simulating disk corruption.",
        f"The marker is {_KNOWLEDGE_MARKER}.",
    )
    expires_at = (dt_util.utcnow() + timedelta(hours=1)).isoformat()
    await old_temporary.async_add(
        _SCOPE_ID,
        _TEMPORARY_MARKER,
        expires_at,
        "acceptance",
        owner_scope_id=_SCOPE_ID,
    )
    await hass.async_block_till_done()

    knowledge_key = (
        f"{KNOWLEDGE_STORAGE_KEY_PREFIX}.{entry.entry_id}.{subentry_id}"
    )
    knowledge_path = Path(hass.config.config_dir) / ".storage" / knowledge_key
    assert knowledge_path.exists()

    # Cross a genuine config-entry unload boundary first. Then evict subsystem-manager
    # objects from hass.data so the next setup cannot accidentally reuse warmed state.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is None

    _purge_cached_managers(hass.data)
    assert not _contains_cached_manager(hass.data)

    # Damage the actual Home Assistant Store file, not an injected storage adapter.
    # The malformed JSON forces the next freshly-created Knowledge Store to exercise
    # Home Assistant's real persistence/parse failure path.
    knowledge_path.write_text('{"version": 2, "data": ', encoding="utf-8")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    restarted = conversation.async_get_agent(hass, entry.entry_id)
    assert restarted is not None
    assert restarted is not agent

    # Knowledge alone is degraded. Its previous warmed object must not have hidden
    # the disk corruption, while sibling managers are newly constructed and healthy.
    assert restarted._knowledge is None
    assert restarted._temporary_memory is not None
    assert restarted._temporary_memory is not old_temporary
    assert restarted._usage is not None
    assert restarted._usage is not old_usage

    statuses = hass.data[SUBSYSTEM_STATUS_KEY][(entry.entry_id, subentry_id)]
    assert statuses["knowledge"]["configured"] is True
    assert statuses["knowledge"]["status"] == "failed"
    assert statuses["temporary_memory"]["configured"] is True
    assert statuses["temporary_memory"]["status"] == "healthy"

    restored_temporary = await restarted._temporary_memory.async_active(
        _SCOPE_ID, owner_scope_id=_SCOPE_ID
    )
    assert [record.content for record in restored_temporary] == [_TEMPORARY_MARKER]

    # Finally prove the degraded fresh-start agent still completes a real public HA
    # Conversation turn. The surviving temporary record reaches the provider prompt,
    # while Knowledge tools are absent because only that subsystem failed startup.
    wire = _install_wire(
        monkeypatch,
        restarted,
        [_chat_sse_text("The healthy subsystems are still available.")],
    )
    result = await conversation.async_converse(
        hass=hass,
        text="Are the remaining subsystems healthy?",
        conversation_id=None,
        context=Context(user_id=_USER_ID),
        language="en",
        agent_id=entry.entry_id,
    )

    assert _speech(result) == "The healthy subsystems are still available."
    assert len(wire.requests) == 1
    request_body = wire.requests[0]["body"]
    assert _TEMPORARY_MARKER in _system_prompt(request_body)
    assert {
        "knowledge_search",
        "knowledge_list",
        "knowledge_get",
    }.isdisjoint(_provider_tool_names(request_body))

    # The intentionally corrupt Store remains corrupt; startup isolation must not
    # silently overwrite the failed subsystem while proving sibling availability.
    assert knowledge_path.read_text(encoding="utf-8") == '{"version": 2, "data": '
