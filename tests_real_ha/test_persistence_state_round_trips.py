"""Persistence and state round-trip acceptance tests against real Home Assistant."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses import (
    conversation_archive as archive_module,
    guest_mode as guest_mode_module,
    knowledge as knowledge_module,
    memory as memory_module,
    request_rules as request_rules_module,
    temporary_memory as temporary_memory_module,
    usage as usage_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPORARY_MEMORY,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


OWNER_SCOPE = "user:alice"


def _conversation_entry(title: str = "Persistence") -> MockConfigEntry:
    """Build one fully local conversation entry with durable subsystems enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_API_KEY: "sk-persistence-acceptance",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {
                    CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
                    CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
                    CONF_KNOWLEDGE_ENABLED: True,
                    CONF_ARCHIVE_ENABLED: True,
                },
                "subentry_type": "conversation",
                "title": f"{title} Conversation",
                "unique_id": None,
            }
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up an entry through Home Assistant's real config-entry manager."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _subentry_id(entry: MockConfigEntry) -> str:
    return next(iter(entry.subentries.values())).subentry_id


def _evict_agent_runtime_managers(hass: HomeAssistant) -> None:
    """Simulate process-local manager loss while retaining Home Assistant storage."""
    for key in (
        memory_module._MEMORY_MANAGERS,
        knowledge_module._KNOWLEDGE_MANAGERS,
        temporary_memory_module._MANAGERS,
        guest_mode_module._MANAGERS,
        request_rules_module._MANAGERS,
        request_rules_module._RUNTIMES,
        usage_module._USAGE_MANAGERS,
        archive_module._ARCHIVE_MANAGERS,
    ):
        hass.data.pop(key, None)


def _durable_rule() -> dict:
    """Return a representative persisted Request Rule without provider I/O."""
    return {
        "id": "good-night",
        "name": "Good night",
        "enabled": True,
        "phrases": ["good night"],
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "domain": "script",
                    "service": "turn_on",
                    "target": {"entity_id": ["script.goodnight"]},
                    "data": {},
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed safely",
        },
        "matching_behavior": "defaults",
        "matching": dict(request_rules_module.DEFAULT_MATCHING),
        "order": 0,
    }


@pytest.mark.asyncio
async def test_real_ha_unload_reload_rehydrates_durable_agent_state(
    hass: HomeAssistant,
) -> None:
    """Every major per-agent durable state family survives a genuinely fresh runtime."""
    entry = _conversation_entry()
    await _setup_entry(hass, entry)
    subentry_id = _subentry_id(entry)

    first = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(first, ExtendedOpenAIAgentEntity)
    assert first._memory is not None
    assert first._temporary_memory is not None
    assert first._knowledge is not None
    assert first._archive is not None
    assert first._guest_mode is not None
    assert first._request_rules is not None

    await first._memory.async_add(
        OWNER_SCOPE,
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        importance="high",
        subject="Oscar",
        key="pet.oscar.breed",
        valid_from="2026-09-01T12:00:00+00:00",
    )
    source = await first._knowledge.async_create(
        "House notes",
        "Stable household reference",
        "The spare keys are in the blue drawer.",
        enabled=False,
    )
    await first._temporary_memory.async_add(
        "device:kitchen",
        "A parcel is expected this afternoon.",
        (dt_util.utcnow() + timedelta(hours=6)).isoformat(),
        "delivery",
        owner_scope_id=OWNER_SCOPE,
    )
    await first._guest_mode.async_update_trusted(
        active_from=(dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        active_until=(dt_util.utcnow() + timedelta(hours=3)).isoformat(),
    )
    await first._request_rules.async_create(_durable_rule())

    async with first._usage.async_run(
        home_assistant_conversation_id="conversation-round-trip",
        source_device_id="satellite-kitchen",
    ):
        await first._usage.async_record_request(
            successful=True,
            usage=usage_module.RequestUsage(
                input_tokens=12,
                output_tokens=4,
                total_tokens=16,
                cached_input_tokens=3,
                reasoning_tokens=2,
            ),
            provider="openai",
            model="gpt-5-mini",
            api_mode="responses",
            request_stage="initial",
            tool_calls_requested=1,
        )

    scope = user_scope("alice", source="authenticated", device_id="satellite-kitchen")
    session = await first._archive.async_begin_session(
        "device:kitchen",
        scope,
        "conversation-round-trip",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert session is not None
    await first._archive.async_record_turn(
        session.session_id,
        run_id="run-round-trip",
        user_text="Where are the spare keys?",
        assistant_text="They are in the blue drawer.",
        successful=True,
    )

    before = {
        "memory": await first._memory.async_backup_data(),
        "knowledge": await first._knowledge.async_backup_data(),
        "temporary_memory": await first._temporary_memory.async_backup_data(),
        "guest_mode": await first._guest_mode.async_backup_data(),
        "request_rules": await first._request_rules.async_backup_data(),
        "usage": await first._usage.async_backup_data(),
        "archive": await first._archive.async_get(
            scope.scope_id, session.session_id, limit=10
        ),
    }

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

    # Normal in-process reloads may reuse manager singletons. Remove only those
    # process-local objects so this setup must reconstruct state from HA's real
    # .storage files, matching the persistence boundary exercised by a restart.
    _evict_agent_runtime_managers(hass)
    await _setup_entry(hass, entry)

    second = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(second, ExtendedOpenAIAgentEntity)
    assert second is not first
    assert second._memory is not None and second._memory is not first._memory
    assert second._temporary_memory is not None
    assert second._knowledge is not None
    assert second._archive is not None
    assert second._guest_mode is not None
    assert second._request_rules is not None

    after = {
        "memory": await second._memory.async_backup_data(),
        "knowledge": await second._knowledge.async_backup_data(),
        "temporary_memory": await second._temporary_memory.async_backup_data(),
        "guest_mode": await second._guest_mode.async_backup_data(),
        "request_rules": await second._request_rules.async_backup_data(),
        "usage": await second._usage.async_backup_data(),
        "archive": await second._archive.async_get(
            scope.scope_id, session.session_id, limit=10
        ),
    }
    assert after == before

    # Rebuilt derived indexes/selection state must also behave like the pre-reload
    # runtime, not merely deserialize into superficially equal dictionaries.
    memories = await second._memory.async_search(OWNER_SCOPE, "Oscar Cavachon")
    assert [item.content for item in memories] == ["Oscar is a Cavachon."]
    assert (await second._knowledge.async_get(source.source_id)).enabled is False
    assert await second._knowledge.async_search("spare keys") == []
    temporary = await second._temporary_memory.async_active(
        "device:kitchen", owner_scope_id=OWNER_SCOPE
    )
    assert [item.content for item in temporary] == [
        "A parcel is expected this afternoon."
    ]
    rule_match = second._request_rules.match("good night")
    assert rule_match is not None and rule_match.rule["id"] == "good-night"
    assert second._usage.totals.total_tokens == 16
    assert second._usage.latest_run is not None
    archive_search = await second._archive.async_search(
        scope.scope_id, "spare keys", limit=5
    )
    assert [item["session_id"] for item in archive_search["results"]] == [
        session.session_id
    ]


@pytest.mark.asyncio
async def test_real_ha_repeated_store_reload_is_idempotent(hass: HomeAssistant) -> None:
    """Repeated load/save boundaries do not progressively normalize or lose state."""
    entry_id = "idempotence-entry"
    subentry_id = "idempotence-agent"

    memory = await memory_module.async_get_memory(hass, entry_id, subentry_id)
    await memory.async_add(
        OWNER_SCOPE,
        "The user prefers Celsius.",
        "preferences",
        "explicit",
        importance="normal",
        subject="temperature units",
        key="preference.temperature.units",
    )
    knowledge = await knowledge_module.async_get_knowledge(hass, entry_id, subentry_id)
    await knowledge.async_create(
        "Appliances", "Kitchen reference", "The oven is a Neff B57CR22N0B."
    )
    rules = await request_rules_module.async_get_request_rules(
        hass, entry_id, subentry_id
    )
    await rules.async_create(_durable_rule())

    expected = {
        "memory": await memory.async_backup_data(),
        "knowledge": await knowledge.async_backup_data(),
        "rules": await rules.async_backup_data(),
    }

    for _ in range(2):
        _evict_agent_runtime_managers(hass)
        memory = await memory_module.async_get_memory(hass, entry_id, subentry_id)
        knowledge = await knowledge_module.async_get_knowledge(
            hass, entry_id, subentry_id
        )
        rules = await request_rules_module.async_get_request_rules(
            hass, entry_id, subentry_id
        )
        assert {
            "memory": await memory.async_backup_data(),
            "knowledge": await knowledge.async_backup_data(),
            "rules": await rules.async_backup_data(),
        } == expected


@pytest.mark.asyncio
async def test_transient_request_routing_state_does_not_cross_restart_boundary(
    hass: HomeAssistant,
) -> None:
    """Durable rules survive manager recreation while conversation overrides do not."""
    entry_id = "routing-state-entry"
    subentry_id = "routing-state-agent"
    rules = await request_rules_module.async_get_request_rules(
        hass, entry_id, subentry_id
    )
    await rules.async_create(_durable_rule())
    durable_before = await rules.async_backup_data()

    runtime = request_rules_module.get_request_rule_runtime(
        hass, entry_id, subentry_id
    )
    runtime.set("conversation:one", {"chat_model": "gpt-4.1-mini"})
    assert runtime.get("conversation:one") == {"chat_model": "gpt-4.1-mini"}

    hass.data.pop(request_rules_module._MANAGERS, None)
    hass.data.pop(request_rules_module._RUNTIMES, None)

    reloaded_rules = await request_rules_module.async_get_request_rules(
        hass, entry_id, subentry_id
    )
    reloaded_runtime = request_rules_module.get_request_rule_runtime(
        hass, entry_id, subentry_id
    )

    assert await reloaded_rules.async_backup_data() == durable_before
    assert reloaded_rules.match("good night") is not None
    assert reloaded_runtime.get("conversation:one") == {}
