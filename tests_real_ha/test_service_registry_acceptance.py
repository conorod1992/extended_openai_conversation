"""Acceptance tests for ExtendedOpenAI's registered Home Assistant services."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses import (
    intercom_services,
    services,
    skills,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_MODE,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
    SERVICE_DOWNLOAD_SKILL,
    SERVICE_MEMORY_CLEAR,
    SERVICE_MEMORY_DELETE,
    SERVICE_MEMORY_LIST,
    SERVICE_PROCESS,
    SERVICE_QUERY_IMAGE,
    SERVICE_RELOAD_SKILLS,
)
from custom_components.extended_openai_conversation_responses.memory import async_get_memory


def _entry(*, memory: bool = False) -> MockConfigEntry:
    """Build one local-only entry suitable for service acceptance tests."""
    conversation_data = {CONF_MEMORY_MODE: MEMORY_MODE_MANUAL} if memory else {}
    return MockConfigEntry(
        domain=DOMAIN,
        title="Service Registry Acceptance",
        data={
            CONF_API_KEY: "sk-service-acceptance",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": conversation_data,
                "subentry_type": "conversation",
                "title": "Service Registry Conversation",
                "unique_id": None,
            }
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Load the integration through Home Assistant's config-entry manager."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _conversation_subentry(entry: MockConfigEntry):
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


def _conversation_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    row = next(
        row
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.domain == "conversation"
    )
    return row.entity_id


async def _response_service_call(
    hass: HomeAssistant,
    service: str,
    data: dict,
    *,
    user_id: str | None = None,
):
    """Call a response service through HA's real service registry."""
    return await hass.services.async_call(
        DOMAIN,
        service,
        data,
        blocking=True,
        context=Context(user_id=user_id),
        return_response=True,
    )


@pytest.mark.asyncio
async def test_memory_services_scope_reads_deletes_and_clears_to_caller(
    hass: HomeAssistant,
) -> None:
    """Memory service calls must derive ownership from the HA Context."""
    entry = _entry(memory=True)
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)

    alice = MockUser(id="memory-alice", name="Memory Alice")
    bob = MockUser(id="memory-bob", name="Memory Bob")
    alice.add_to_hass(hass)
    bob.add_to_hass(hass)

    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    alice_created = await memory.async_add(
        alice.id, "Alice owns the blue notebook.", "context", "explicit"
    )
    bob_created = await memory.async_add(
        bob.id, "Bob owns the green notebook.", "context", "explicit"
    )
    alice_id = alice_created["memory"]["memory_id"]
    bob_id = bob_created["memory"]["memory_id"]

    base = {"config_entry": entry.entry_id, "agent_id": subentry.subentry_id}
    listed = await _response_service_call(
        hass, SERVICE_MEMORY_LIST, base, user_id=alice.id
    )
    assert [item["memory_id"] for item in listed["memories"]] == [alice_id]
    assert listed["memories"][0]["content"] == "Alice owns the blue notebook."

    cross_user_delete = await _response_service_call(
        hass,
        SERVICE_MEMORY_DELETE,
        {**base, "memory_ids": [bob_id]},
        user_id=alice.id,
    )
    assert cross_user_delete == {"deleted": 0}

    cleared = await _response_service_call(
        hass,
        SERVICE_MEMORY_CLEAR,
        {**base, "confirm": True},
        user_id=alice.id,
    )
    assert cleared == {"deleted": 1}

    alice_after = await _response_service_call(
        hass, SERVICE_MEMORY_LIST, base, user_id=alice.id
    )
    bob_after = await _response_service_call(
        hass, SERVICE_MEMORY_LIST, base, user_id=bob.id
    )
    assert alice_after == {"memories": []}
    assert [item["memory_id"] for item in bob_after["memories"]] == [bob_id]


@pytest.mark.asyncio
async def test_memory_service_schema_and_manager_failures_surface_through_registry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HA validation and manager failures must not be bypassed by service routing."""
    entry = _entry(memory=True)
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    user = MockUser(id="memory-user", name="Memory User")
    user.add_to_hass(hass)
    base = {"config_entry": entry.entry_id, "agent_id": subentry.subentry_id}

    with pytest.raises(HomeAssistantError):
        await _response_service_call(
            hass,
            SERVICE_MEMORY_LIST,
            {**base, "limit": 0},
            user_id=user.id,
        )

    unavailable = AsyncMock(
        side_effect=HomeAssistantError("Memory manager is unavailable")
    )
    monkeypatch.setattr(services, "async_get_memory", unavailable)
    with pytest.raises(HomeAssistantError, match="Memory manager is unavailable"):
        await _response_service_call(
            hass,
            SERVICE_MEMORY_LIST,
            base,
            user_id=user.id,
        )
    unavailable.assert_awaited_once_with(hass, entry.entry_id, subentry.subentry_id)


@pytest.mark.asyncio
async def test_change_config_service_enforces_admin_and_updates_entry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_admin_user: MockUser,
    hass_read_only_user: MockUser,
) -> None:
    """Configuration mutation must pass through HA identity and provider validation."""
    entry = _entry()
    await _setup_entry(hass, entry)
    provider_validation = AsyncMock(return_value=object())
    monkeypatch.setattr(services, "get_authenticated_client", provider_validation)

    request = {
        "config_entry": entry.entry_id,
        CONF_ORGANIZATION: "acceptance-org",
    }
    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await hass.services.async_call(
            DOMAIN,
            "change_config",
            request,
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    provider_validation.assert_not_awaited()
    assert CONF_ORGANIZATION not in entry.data

    await hass.services.async_call(
        DOMAIN,
        "change_config",
        request,
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    assert entry.data[CONF_ORGANIZATION] == "acceptance-org"
    provider_validation.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_config_service_rejects_concurrent_stale_update(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_admin_user: MockUser,
) -> None:
    """A service call must not overwrite config changed during validation."""
    entry = _entry()
    await _setup_entry(hass, entry)

    async def validate_with_concurrent_change(**_kwargs):
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_ORGANIZATION: "concurrent-winner"},
        )
        return object()

    monkeypatch.setattr(
        services, "get_authenticated_client", validate_with_concurrent_change
    )

    with pytest.raises(HomeAssistantError, match="changed while this configuration"):
        await hass.services.async_call(
            DOMAIN,
            "change_config",
            {
                "config_entry": entry.entry_id,
                CONF_ORGANIZATION: "stale-request",
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert entry.data[CONF_ORGANIZATION] == "concurrent-winner"
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_broadcast_service_routes_context_and_rejects_invalid_targets(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_admin_user: MockUser,
) -> None:
    """Broadcast must resolve targets, authorize the caller, and queue only them."""
    entry = _entry()
    await _setup_entry(hass, entry)

    manager = SimpleNamespace(
        resolve_targets=MagicMock(return_value=["assist_satellite.kitchen"]),
        async_send=AsyncMock(return_value={"id": "broadcast-acceptance"}),
    )
    monkeypatch.setattr(
        intercom_services,
        "async_get_intercom",
        AsyncMock(return_value=manager),
    )

    response = await _response_service_call(
        hass,
        intercom_services.SERVICE_BROADCAST,
        {
            "message": "Dinner is ready",
            "entity_id": ["assist_satellite.kitchen"],
            "ttl_seconds": 30,
        },
        user_id=hass_admin_user.id,
    )
    assert response == {"id": "broadcast-acceptance"}
    manager.resolve_targets.assert_called_once_with(
        whole_home=False,
        entity_ids=["assist_satellite.kitchen"],
        device_ids=[],
        area_ids=[],
        floor_ids=[],
        label_ids=[],
        origin_entity_id=None,
        origin_device_id=None,
    )
    manager.async_send.assert_awaited_once_with(
        "Dinner is ready",
        entity_ids=["assist_satellite.kitchen"],
        origin_entity_id=None,
        origin_device_id=None,
        source="service",
        ttl_seconds=30,
    )

    manager.resolve_targets.reset_mock(return_value=True)
    manager.resolve_targets.return_value = []
    manager.async_send.reset_mock()
    with pytest.raises(HomeAssistantError, match="No matching announcement-capable"):
        await _response_service_call(
            hass,
            intercom_services.SERVICE_BROADCAST,
            {
                "message": "Nobody should hear this",
                "entity_id": ["assist_satellite.missing"],
            },
            user_id=hass_admin_user.id,
        )
    manager.async_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_service_denies_caller_without_control_permission(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_read_only_user: MockUser,
) -> None:
    """A real service Context must reach Broadcast's HA permission boundary."""
    entry = _entry()
    await _setup_entry(hass, entry)

    manager = SimpleNamespace(
        resolve_targets=MagicMock(return_value=["assist_satellite.kitchen"]),
        async_send=AsyncMock(return_value={"id": "must-not-send"}),
    )
    monkeypatch.setattr(
        intercom_services,
        "async_get_intercom",
        AsyncMock(return_value=manager),
    )

    with pytest.raises(HomeAssistantError, match="does not have permission"):
        await _response_service_call(
            hass,
            intercom_services.SERVICE_BROADCAST,
            {
                "message": "Restricted",
                "entity_id": ["assist_satellite.kitchen"],
            },
            user_id=hass_read_only_user.id,
        )
    manager.async_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_skill_services_validate_admin_input_and_reload_via_registry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_admin_user: MockUser,
    hass_read_only_user: MockUser,
) -> None:
    """Skill services must retain HA auth, validation, and response semantics."""
    entry = _entry()
    await _setup_entry(hass, entry)

    manager = SimpleNamespace(
        async_load_skills=AsyncMock(),
        get_all_skills=MagicMock(return_value=[object(), object()]),
    )
    get_instance = AsyncMock(return_value=manager)
    monkeypatch.setattr(skills.SkillManager, "async_get_instance", get_instance)

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await _response_service_call(
            hass,
            SERVICE_RELOAD_SKILLS,
            {},
            user_id=hass_read_only_user.id,
        )
    get_instance.assert_not_awaited()

    reloaded = await _response_service_call(
        hass,
        SERVICE_RELOAD_SKILLS,
        {},
        user_id=hass_admin_user.id,
    )
    assert reloaded == {"loaded_skills": 2}
    manager.async_load_skills.assert_awaited_once()

    with pytest.raises(HomeAssistantError, match="letters, numbers"):
        await _response_service_call(
            hass,
            SERVICE_DOWNLOAD_SKILL,
            {"skill_name": "../escape"},
            user_id=hass_admin_user.id,
        )


@pytest.mark.asyncio
async def test_query_image_service_rejects_malformed_images_before_provider_call(
    hass: HomeAssistant,
    hass_admin_user: MockUser,
) -> None:
    """The real service registry must enforce query-image attachment validation."""
    entry = _entry()
    await _setup_entry(hass, entry)

    with pytest.raises(HomeAssistantError):
        await _response_service_call(
            hass,
            SERVICE_QUERY_IMAGE,
            {
                "config_entry": entry.entry_id,
                "prompt": "Describe this",
                "images": [],
            },
            user_id=hass_admin_user.id,
        )


@pytest.mark.asyncio
async def test_process_service_fails_cleanly_for_unloaded_entry(
    hass: HomeAssistant,
) -> None:
    """Shared service registration must not keep an unloaded agent callable."""
    entry = _entry()
    await _setup_entry(hass, entry)
    agent_entity_id = _conversation_entity_id(hass, entry)

    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)

    with pytest.raises(HomeAssistantError, match="conversation agent not found"):
        await _response_service_call(
            hass,
            SERVICE_PROCESS,
            {
                "text": "This must not run",
                "agent_id": agent_entity_id,
            },
        )
