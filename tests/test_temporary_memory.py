"""Temporary-memory lifecycle, scoping, and safety tests."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_PROMPT,
    CONF_TEMPORARY_MEMORY,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from homeassistant.util import dt as dt_util


class Storage:
    def __init__(self, data=None):
        self.data = data

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def future(hours=1) -> str:
    return (dt_util.utcnow() + timedelta(hours=hours)).isoformat()


async def test_active_records_are_scoped_persisted_and_bounded() -> None:
    storage = Storage()
    memory = TemporaryMemory(storage)
    await memory.async_initialize()
    created = await memory.async_add(
        "device:kitchen", "Cooking pasta", future(), "activity"
    )
    assert [item.content for item in await memory.async_active("device:kitchen")] == [
        "Cooking pasta"
    ]
    assert await memory.async_active("device:bedroom") == []

    restored = TemporaryMemory(Storage(storage.data))
    await restored.async_initialize()
    assert len(await restored.async_active("device:kitchen")) == 1
    assert (
        await restored.async_delete("device:bedroom", [created["memory"]["memory_id"]])
        == 0
    )


async def test_expiry_pruned_at_startup_and_before_injection() -> None:
    expired = {
        "memory_id": "old",
        "scope_id": "user:alice",
        "content": "Waiting for a parcel",
        "category": "delivery",
        "source": "automatic",
        "expires_at": (dt_util.utcnow() - timedelta(minutes=1)).isoformat(),
        "created_at": dt_util.utcnow().isoformat(),
        "updated_at": dt_util.utcnow().isoformat(),
    }
    memory = TemporaryMemory(Storage({"records": [expired]}))
    await memory.async_initialize()
    assert await memory.async_active("user:alice") == []
    assert memory.expired_pruned == 1


async def test_update_supersedes_and_secret_is_rejected() -> None:
    memory = TemporaryMemory(Storage())
    await memory.async_initialize()
    created = await memory.async_add(
        "user:alice", "Parents are visiting this weekend", future(24), "visitors"
    )
    updated = await memory.async_update(
        "user:alice",
        created["memory"]["memory_id"],
        "Parents are visiting next weekend",
        future(48),
        None,
    )
    assert updated.content.endswith("next weekend")
    with pytest.raises(ValueError):
        await memory.async_add("user:alice", "My PIN is 1234", future())


def test_balanced_prompt_infers_weekend_expiry_without_clarification() -> None:
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = MagicMock()
    entity.hass.config.time_zone = "Europe/Dublin"
    entity.skill_manager = SimpleNamespace(get_all_skills=lambda: [])
    entity.subentry = SimpleNamespace(
        data={
            CONF_PROMPT: "Base prompt",
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        }
    )
    prompt = entity._build_system_prompt(
        [],
        SimpleNamespace(device_id=None),
        SimpleNamespace(extra_system_prompt=None),
    )
    assert "instead of asking unnecessary clarification" in prompt
    assert 'for "this weekend" use the end of Sunday' in prompt
    assert "Europe/Dublin" in prompt
