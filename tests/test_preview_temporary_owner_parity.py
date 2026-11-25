from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as runtime,
    temporary_memory as temporary,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_CONTINUITY,
    CONF_KNOWLEDGE_ENABLED,
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.management_request_preview import (
    async_preview_effective_request,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from tests.test_prompt_preview import (
    _entity_context,
    _options,
    _setup_area_template_registries,
    _temporary,
)


@pytest.mark.parametrize(
    "mode",
    [
        CONVERSATION_CONTINUITY_USER,
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        CONVERSATION_CONTINUITY_DEVICE,
    ],
)
@pytest.mark.asyncio
async def test_preview_includes_temporary_memory_available_to_same_owner(
    hass, monkeypatch, mode
):
    _setup_area_template_registries(hass)
    hass.config.location_name = "Current Home"
    hass.config.time_zone = "Europe/Dublin"
    options = _options()
    options[CONF_CONVERSATION_CONTINUITY] = mode
    options[CONF_KNOWLEDGE_ENABLED] = False
    record = replace(
        _temporary(),
        owner_scope_id="user:admin",
        expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    store = SimpleNamespace(
        async_load=AsyncMock(return_value={"records": [asdict(record)]}),
        async_save=AsyncMock(),
    )
    manager = TemporaryMemory(store)
    await manager.async_initialize()
    manager.async_active = AsyncMock(wraps=manager.async_active)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_request_preview.get_loaded_temporary_memory",
        lambda *_args: manager,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_request_preview.get_exposed_entities",
        lambda _hass: _entity_context(),
    )

    # Mirror conversation.py's request-derived scope: HA-default still receives
    # a conversation fallback, while owner identity remains user:admin.
    scope = user_scope("admin", source="authenticated_user")
    continuity_key, _ = ConversationContinuity.identity_key(mode, scope, None)
    runtime_scope = continuity_key or "conversation:actual-ha-chat-id"
    entity = runtime.ExtendedOpenAIAgentEntity.__new__(
        runtime.ExtendedOpenAIAgentEntity
    )
    entity.hass = hass
    entity.subentry = SimpleNamespace(subentry_id="agent-1", data=options)
    entity._temporary_memory = manager
    entity._knowledge = None
    entity.skill_manager = SimpleNamespace(get_all_skills=lambda: [])
    owner_token = runtime._ACTIVE_TURN.set(
        replace(runtime._ACTIVE_TURN.get(), scope=scope)
    )
    temporary_token = runtime._ACTIVE_TURN.set(
        replace(runtime._ACTIVE_TURN.get(), temporary_scope=runtime_scope)
    )
    try:
        actual_memories = await entity._async_load_temporary_memories()
        production_prompt = entity._build_system_prompt(
            _entity_context(),
            SimpleNamespace(device_id=None),
            None,
            temporary_memories=actual_memories,
        )
    finally:
        runtime._ACTIVE_TURN.reset(temporary_token)
        runtime._ACTIVE_TURN.reset(owner_token)
    assert record.content in production_prompt
    manager.async_active.assert_awaited_once_with(runtime_scope)

    preview = await async_preview_effective_request(
        hass,
        SimpleNamespace(entry_id="entry-1"),
        entity.subentry,
        options,
        "admin",
    )
    missing = []
    if record.content not in preview["prompt"]:
        missing.append("active temporary fact")
    if "temporary_memory_add" not in str(preview["sections"]):
        missing.append("temporary_memory_add tool schema")
    assert not missing, (
        f"Preview dropped {missing}, which production includes for {mode}; "
        f"notes={preview['notes']}"
    )


@pytest.mark.parametrize(
    ("scope_id", "stored_owner", "personal"),
    [
        ("user:admin", None, True),
        ("shared:household", None, False),
        ("user:admin", " user:admin ", True),
    ],
)
async def test_cold_preview_matches_live_temporary_memory(
    hass, monkeypatch, scope_id, stored_owner, personal
):
    _setup_area_template_registries(hass)
    hass.config.location_name = "Current Home"
    hass.config.time_zone = "Europe/Dublin"
    options = _options()
    options[CONF_KNOWLEDGE_ENABLED] = False
    options["temporary_memory"] = TEMPORARY_MEMORY_BALANCED
    now = datetime.now(UTC)
    base = replace(
        _temporary(),
        scope_id=scope_id,
        owner_scope_id=stored_owner,
        expires_at=(now + timedelta(days=1)).isoformat(),
    )
    retained = replace(
        base,
        memory_id="keep",
        content="A retained temporary fact.",
        updated_at=now.isoformat(),
    )
    overflow = replace(
        base,
        memory_id="overflow",
        content="An overflow temporary fact.",
        updated_at=(now - timedelta(days=1)).isoformat(),
    )
    expired = replace(
        retained,
        memory_id="expired",
        content="An expired temporary fact.",
        expires_at=(now - timedelta(seconds=1)).isoformat(),
    )
    unrelated = replace(
        retained,
        memory_id="other",
        scope_id="user:bob",
        owner_scope_id="user:bob",
        content="Someone else's temporary fact.",
    )
    store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={
                "records": [
                    asdict(row) for row in (retained, overflow, expired, unrelated)
                ]
            }
        ),
        async_save=AsyncMock(),
    )
    monkeypatch.setattr(temporary, "MAX_ACTIVE_RECORDS", 2)
    monkeypatch.setattr(temporary, "_temporary_memory_store", lambda *_: store)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_request_preview.get_exposed_entities",
        lambda _hass: _entity_context(),
    )
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1", data=options)
    cold = await temporary.async_read_temporary_memory_snapshot(
        hass, entry.entry_id, subentry.subentry_id, scope_id, owner_scope_id=scope_id
    )
    cold_preview = (
        await async_preview_effective_request(hass, entry, subentry, options, "admin")
        if personal
        else None
    )
    store.async_save.assert_not_awaited()
    assert (
        temporary.get_loaded_temporary_memory(
            hass, entry.entry_id, subentry.subentry_id
        )
        is None
    )
    manager = await temporary.async_get_temporary_memory(
        hass, entry.entry_id, subentry.subentry_id
    )
    warm = await manager.async_active_snapshot(scope_id, owner_scope_id=scope_id)
    assert cold == warm
    assert [row.memory_id for row in cold] == [retained.memory_id]
    if cold_preview is not None:
        warm_preview = await async_preview_effective_request(
            hass, entry, subentry, options, "admin"
        )
        for preview in (cold_preview, warm_preview):
            assert retained.content in preview["prompt"]
            assert all(
                row.content not in preview["prompt"]
                for row in (overflow, expired, unrelated)
            )
