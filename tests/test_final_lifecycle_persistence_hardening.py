"""Regression tests for the final lifecycle and persistence hardening sweep."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import openai
import pytest

from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import async_unload_entry
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.openai_compat import (
    apply_openai_compatibility,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    install_persistence_transactions,
)
from custom_components.extended_openai_conversation_responses.template import (
    ExtendedOpenAITemplateManager,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)


class ToggleStorage:
    """Detached storage double that can fail writes after initialization."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = deepcopy(data)
        self.fail_saves = False

    async def async_load(self) -> dict[str, Any] | None:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_saves:
            raise RuntimeError("simulated Store failure")
        self.data = deepcopy(data)


async def test_memory_save_failure_rolls_back_fact_and_indexes() -> None:
    """A failed durable write cannot leak the attempted memory update in-process."""
    install_persistence_transactions()
    storage = ToggleStorage()
    manager = PersistentMemory(storage)
    await manager.async_initialize()
    created = await manager.async_add(
        "user-1", "The kitchen light is warm white.", "home", "explicit"
    )
    memory_id = created["memory"]["memory_id"]

    storage.fail_saves = True
    with pytest.raises(RuntimeError, match="simulated Store failure"):
        await manager.async_update(
            "user-1", memory_id, "The kitchen light is bright blue."
        )

    assert [item.content for item in await manager.async_list("user-1")] == [
        "The kitchen light is warm white."
    ]
    assert [
        item.memory_id for item in await manager.async_search("user-1", "warm white")
    ] == [memory_id]
    assert await manager.async_search("user-1", "bright blue") == []


async def test_knowledge_save_failure_rolls_back_source_and_index() -> None:
    """Knowledge search and management views remain at the last durable version."""
    install_persistence_transactions()
    storage = ToggleStorage()
    manager = KnowledgeLibrary(storage)
    await manager.async_initialize()
    source = await manager.async_create(
        "Heating notes", "Reference", "Boiler pressure should be 1.2 bar."
    )

    storage.fail_saves = True
    with pytest.raises(RuntimeError, match="simulated Store failure"):
        await manager.async_update(
            source.source_id, content="Boiler marker zirconium."
        )

    restored = await manager.async_get(source.source_id)
    assert restored.content == "Boiler pressure should be 1.2 bar."
    assert [item.source_id for item in await manager.async_search("1.2 bar")] == [
        source.source_id
    ]
    assert await manager.async_search("zirconium") == []


async def test_temporary_memory_save_failure_rolls_back_record() -> None:
    """Temporary memory also exposes only successfully persisted mutations."""
    install_persistence_transactions()
    storage = ToggleStorage()
    manager = TemporaryMemory(storage)  # type: ignore[arg-type]
    await manager.async_initialize()
    created = await manager.async_add(
        "user-1",
        "The parcel is by the door.",
        (dt_util.utcnow() + timedelta(days=30)).isoformat(),
        "errand",
    )
    memory_id = created["memory"]["memory_id"]

    storage.fail_saves = True
    with pytest.raises(RuntimeError, match="simulated Store failure"):
        await manager.async_update(
            "user-1", memory_id, "The parcel was collected.", None, None
        )

    active = await manager.async_active_snapshot("user-1")
    assert [item.content for item in active] == ["The parcel is by the door."]


async def test_failed_platform_unload_keeps_template_lifecycle_acquired() -> None:
    """A failed platform unload must not tear down global template helpers."""
    config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=False))
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(entry_id="entry-1")

    with patch(
        "custom_components.extended_openai_conversation_responses.async_unload_templates",
        new=AsyncMock(),
    ) as unload_templates:
        assert await async_unload_entry(hass, entry) is False
        unload_templates.assert_not_awaited()


async def test_successful_platform_unload_releases_exact_entry() -> None:
    """Template lifecycle release happens only after platform unload succeeds."""
    config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True))
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(entry_id="entry-2")

    with patch(
        "custom_components.extended_openai_conversation_responses.async_unload_templates",
        new=AsyncMock(return_value=True),
    ) as unload_templates:
        assert await async_unload_entry(hass, entry) is True
        unload_templates.assert_awaited_once_with(hass, "entry-2")


def test_template_manager_tracks_only_loaded_entry_references() -> None:
    """One entry unloading cannot remove globals still required by another entry."""
    manager = ExtendedOpenAITemplateManager(SimpleNamespace())
    manager.acquire("entry-1")
    manager.acquire("entry-2")
    manager.release("entry-1")
    assert manager.in_use is True
    manager.release("entry-2")
    assert manager.in_use is False


def test_openai_245_compatible_provider_usage_is_tolerant() -> None:
    """HA's pinned SDK accepts compatible-provider usage missing the new counter."""
    if openai.__version__ != "2.45.0":
        pytest.skip("Compatibility shim is intentionally scoped to OpenAI 2.45.0")

    apply_openai_compatibility()
    from openai.types.responses.response_usage import InputTokensDetails, ResponseUsage

    details = InputTokensDetails(cached_tokens=3)
    assert details.cache_write_tokens == 0
    usage = ResponseUsage.model_validate(
        {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens": 5,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 15,
        }
    )
    assert usage.input_tokens_details.cache_write_tokens == 0
