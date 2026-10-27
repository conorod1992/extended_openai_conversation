from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as runtime,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_CONTINUITY,
    CONF_KNOWLEDGE_ENABLED,
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
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
