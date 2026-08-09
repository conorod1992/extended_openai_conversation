"""Tests for the on-demand Knowledge Library."""

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_PROMPT,
    DEFAULT_KNOWLEDGE_ENABLED,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.entity import (
    _format_tools,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    MAX_CONTENT_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_GET_CHARACTERS,
    MAX_TITLE_LENGTH,
    KnowledgeLibrary,
    KnowledgeStore,
    knowledge_tools,
)
from custom_components.extended_openai_conversation_responses.knowledge_ui import (
    async_manage_knowledge_command,
)
from homeassistant.exceptions import HomeAssistantError


def test_management_panel_knowledge_limits_match_backend() -> None:
    """Keep browser maxlength constraints aligned with backend validation."""
    panel = (
        Path(__file__).parents[1]
        / "custom_components"
        / "extended_openai_conversation_responses"
        / "frontend"
        / "management-panel.js"
    ).read_text(encoding="utf-8")

    assert f"const KNOWLEDGE_TITLE_LIMIT = {MAX_TITLE_LENGTH};" in panel
    assert f"const KNOWLEDGE_DESCRIPTION_LIMIT = {MAX_DESCRIPTION_LENGTH};" in panel
    assert f"const KNOWLEDGE_LIMIT = {MAX_CONTENT_LENGTH};" in panel
    assert 'maxlength="${KNOWLEDGE_TITLE_LIMIT}"' in panel
    assert 'maxlength="${KNOWLEDGE_DESCRIPTION_LIMIT}"' in panel
    assert 'maxlength="${KNOWLEDGE_LIMIT}"' in panel


class FakeStorage:
    """Small durable storage double."""

    def __init__(self, data=None, delay: float = 0) -> None:
        self.data = deepcopy(data)
        self.delay = delay

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.data = deepcopy(data)


async def _library(storage=None) -> KnowledgeLibrary:
    library = KnowledgeLibrary(storage or FakeStorage())
    await library.async_initialize()
    return library


async def test_empty_initialization_and_crud_persistence() -> None:
    storage = FakeStorage()
    library = await _library(storage)
    assert await library.async_list() == []

    created = await library.async_create(
        "Kitchen layout", "Where household items are stored", "Tea towels: oven drawer."
    )
    assert (await library.async_get(created.source_id)).content.endswith("oven drawer.")
    updated = await library.async_update(
        created.source_id, content="Tea towels: pantry."
    )
    assert updated.source_id == created.source_id
    assert updated.updated_at >= created.updated_at

    reloaded = await _library(storage)
    assert (await reloaded.async_get(created.source_id)).content.endswith("pantry.")
    assert await reloaded.async_delete(created.source_id) is True
    assert await reloaded.async_list() == []


async def test_malformed_stored_records_are_ignored() -> None:
    valid = {
        "source_id": "valid",
        "title": "Tools",
        "description": "Inventory",
        "content": "Hammer in garage",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    library = await _library(FakeStorage({"sources": [{"bad": True}, valid]}))
    assert [item["source_id"] for item in await library.async_list()] == ["valid"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("title", "x" * (MAX_TITLE_LENGTH + 1), "title"),
        ("description", "x" * (MAX_DESCRIPTION_LENGTH + 1), "description"),
        ("content", "x" * (MAX_CONTENT_LENGTH + 1), "content"),
        ("title", "  ", "title"),
        ("content", "\n\t", "content"),
    ],
    ids=[
        "title-too-long",
        "description-too-long",
        "content-too-long",
        "blank-title",
        "blank-content",
    ],
)
async def test_field_limits(field: str, value: str, message: str) -> None:
    library = await _library()
    values = {"title": "Title", "description": "Description", "content": "Content"}
    values[field] = value
    with pytest.raises(ValueError, match=message):
        await library.async_create(**values)


async def test_source_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.knowledge.MAX_SOURCES_PER_AGENT",
        1,
    )
    library = await _library()
    await library.async_create("One", "", "First")
    with pytest.raises(ValueError, match="limit reached"):
        await library.async_create("Two", "", "Second")


async def test_concurrent_writes_do_not_lose_updates() -> None:
    storage = FakeStorage(delay=0.001)
    library = await _library(storage)
    await asyncio.gather(
        *(
            library.async_create(f"Source {index}", "", f"Value {index}")
            for index in range(20)
        )
    )
    assert len(await library.async_list()) == 20
    assert len(storage.data["sources"]) == 20


async def test_agent_isolation() -> None:
    first = await _library(FakeStorage())
    second = await _library(FakeStorage())
    await first.async_create("Private layout", "", "Cupboard above fridge")
    assert await second.async_search("cupboard fridge") == []


async def test_search_ranking_and_description_and_phrase_boost() -> None:
    library = await _library()
    title = await library.async_create(
        "Concrete wall drill", "Tools", "An incidental shelf note"
    )
    content = await library.async_create(
        "Garage note", "Storage", "Concrete wall drill appears in a sentence"
    )
    described = await library.async_create(
        "Equipment", "Masonry bits for concrete walls", "Garage shelf"
    )

    results = await library.async_search("concrete wall drill", limit=3)
    assert results[0].source_id == title.source_id
    assert {result.source_id for result in results} == {
        title.source_id,
        content.source_id,
        described.source_id,
    }
    assert (
        next(
            result for result in results if result.source_id == content.source_id
        ).score
        > 1
    )


async def test_long_source_returns_relevant_bounded_chunk_without_duplicates() -> None:
    library = await _library()
    content = (
        "Unrelated workshop notes.\n\n" * 180
    ) + "Spare tea towels are in the lowest drawer beside the oven."
    source = await library.async_create("Kitchen", "Layout", content)
    results = await library.async_search("spare tea towels", limit=10)
    assert len(results) == 1
    assert results[0].source_id == source.source_id
    assert "lowest drawer" in results[0].excerpt
    assert len(results[0].excerpt) <= 2_000


async def test_search_filter_limit_empty_update_and_delete() -> None:
    library = await _library()
    first = await library.async_create("Kitchen", "", "Tea towels by oven")
    second = await library.async_create("Laundry", "", "Tea towels in basket")
    assert await library.async_search("!!!") == []
    assert [
        r.source_id
        for r in await library.async_search("tea towels", [second.source_id], 1)
    ] == [second.source_id]
    assert {
        result.source_id
        for result in await library.async_search("tea towels", source_ids=[])
    } == {first.source_id, second.source_id}
    await library.async_update(first.source_id, content="First aid kit above fridge")
    assert not await library.async_search("oven", [first.source_id])
    assert await library.async_search("first aid", [first.source_id])
    await library.async_delete(first.source_id)
    assert not await library.async_search("first aid")


async def test_catalog_is_bounded_filterable_paginated_and_content_free() -> None:
    library = await _library()
    first = await library.async_create(
        "Bosch dishwasher", "Model and maintenance procedures", "Secret full manual"
    )
    await library.async_create(
        "Home tools", "Inventory of drills and fixings", "Secret inventory"
    )

    filtered = await library.async_catalog("dishwasher")
    assert filtered["total"] == 1
    assert filtered["sources"][0]["source_id"] == first.source_id
    assert "content" not in filtered["sources"][0]
    assert [
        result.source_id
        for result in await library.async_search("dishwasher", source_ids=["household"])
    ] == [first.source_id]

    first_page = await library.async_catalog(limit=1)
    assert first_page["returned"] == 1
    assert first_page["has_more"] is True
    assert first_page["next_offset"] == 1
    second_page = await library.async_catalog(limit=1, offset=1)
    assert second_page["returned"] == 1
    assert second_page["has_more"] is False
    assert second_page["next_offset"] is None


async def test_get_pagination_and_hard_limit() -> None:
    library = await _library()
    source = await library.async_create("Long", "Reference", "x" * 30_000)
    first = await library.async_get_section(source.source_id, 0, 6_000)
    assert first["returned_characters"] == 6_000
    assert first["total_characters"] == 30_000
    assert first["has_more"] is True
    assert first["next_start_character"] == 6_000
    second = await library.async_get_section(source.source_id, 6_000, 999_999)
    assert second["returned_characters"] == MAX_GET_CHARACTERS
    assert second["next_start_character"] == 26_000
    with pytest.raises(ValueError, match="not found"):
        await library.async_get_section("missing")


async def test_storage_migration_hook() -> None:
    store = KnowledgeStore.__new__(KnowledgeStore)
    old = [{"source_id": "one"}]
    assert await store._async_migrate_func(0, 1, old) == {"sources": old}
    with pytest.raises(NotImplementedError):
        await store._async_migrate_func(99, 1, {})


def _entity(enabled: bool, count: int) -> ExtendedOpenAIAgentEntity:
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.subentry = SimpleNamespace(
        data={CONF_FUNCTION_TOOLS: "[]", CONF_KNOWLEDGE_ENABLED: enabled}
    )
    entity._knowledge = SimpleNamespace(source_count=count)
    return entity


def test_tools_require_enabled_populated_library_and_format_for_both_apis() -> None:
    assert DEFAULT_KNOWLEDGE_ENABLED is False
    assert _entity(False, 1)._get_function_tools() == []
    assert _entity(True, 0)._get_function_tools() == []
    tools = _entity(True, 1)._get_function_tools()
    assert {tool["spec"]["name"] for tool in tools} == {
        "knowledge_search",
        "knowledge_list",
        "knowledge_get",
    }
    assert all(tool["type"] == "function" for tool in _format_tools(tools, "responses"))
    assert all("function" in tool for tool in _format_tools(tools, "chat_completions"))


async def test_tool_get_safe_error_and_pagination() -> None:
    library = await _library()
    source = await library.async_create("Kitchen", "", "Tea towels beside oven")
    entity = _entity(True, 1)
    entity._knowledge = library
    result = await entity._async_execute_knowledge_tool(
        "get", {"source_id": source.source_id, "max_characters": 500}
    )
    assert result["content"] == source.content
    with pytest.raises(ValueError, match="not found"):
        await entity._async_execute_knowledge_tool("get", {"source_id": "bad"})


async def test_tool_list_returns_metadata_without_content() -> None:
    library = await _library()
    await library.async_create(
        "Dishwasher manual", "Bosch model and rinse aid settings", "Private content"
    )
    entity = _entity(True, 1)
    entity._knowledge = library

    result = await entity._async_execute_knowledge_tool(
        "list", {"query": "dishwasher", "limit": 20, "offset": 0}
    )

    assert result["total"] == 1
    assert result["sources"][0]["title"] == "Dishwasher manual"
    assert "content" not in result["sources"][0]


async def test_tool_search_ignores_invented_source_ids_and_reports_fallback() -> None:
    library = await _library()
    source = await library.async_create(
        "Home Devices & Equipment Inventory",
        "Household appliance models",
        "The dishwasher is an Indesit DIE2B19UK.",
    )
    entity = _entity(True, 1)
    entity._knowledge = library

    for invented_id in ("", "household"):
        result = await entity._async_execute_knowledge_tool(
            "search",
            {
                "query": "dishwasher model",
                "source_ids": [invented_id],
                "limit": 5,
            },
        )
        assert result["results"][0]["source_id"] == source.source_id
        assert result["source_filter"] == {
            "applied_source_ids": [],
            "ignored_source_ids": [invented_id],
            "fell_back_to_all_sources": True,
        }


def test_prompt_instructions_only_when_enabled_and_populated(hass) -> None:
    entity = _entity(False, 1)
    entity.hass = hass
    entity.skill_manager = SimpleNamespace(get_all_skills=lambda: [])
    entity.subentry.data[CONF_PROMPT] = "Base prompt"
    context = SimpleNamespace(device_id=None)
    user_input = SimpleNamespace(extra_system_prompt=None)
    assert entity._build_system_prompt([], context, user_input) == "Base prompt"

    entity.subentry.data[CONF_KNOWLEDGE_ENABLED] = True
    prompt = entity._build_system_prompt([], context, user_input)
    assert "Knowledge Library" in prompt
    assert "knowledge_search" in prompt
    assert "knowledge_list" in prompt
    assert "short, discriminative keywords" in prompt
    assert "available knowledge sections" in prompt
    assert "Never invent an ID" in prompt
    assert 'descriptive word such as "household"' in prompt
    assert "knowledge_list with\n  no query first" in prompt
    assert "untrusted reference data" in prompt
    assert "Tea towels" not in prompt


def _management_hass():
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Family assistant",
        data={CONF_KNOWLEDGE_ENABLED: True},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain="extended_openai_conversation_responses",
        title="OpenAI",
        subentries={"agent-1": subentry},
    )
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry
    hass.config_entries.async_entries.return_value = [entry]
    return hass


async def test_management_api_crud_and_list_omits_content() -> None:
    hass = _management_hass()
    library = await _library()
    base = {"entry_id": "entry-1", "subentry_id": "agent-1"}
    with patch(
        "custom_components.extended_openai_conversation_responses.knowledge_ui.async_get_knowledge",
        AsyncMock(return_value=library),
    ):
        created = await async_manage_knowledge_command(
            hass,
            {
                **base,
                "action": "create",
                "title": "Kitchen",
                "description": "Layout",
                "content": "Tea towels beside oven",
            },
        )
        source_id = created["source"]["source_id"]
        listed = await async_manage_knowledge_command(hass, {**base, "action": "list"})
        assert "content" not in listed["sources"][0]
        fetched = await async_manage_knowledge_command(
            hass, {**base, "action": "get", "source_id": source_id}
        )
        assert fetched["source"]["content"] == "Tea towels beside oven"
        await async_manage_knowledge_command(
            hass,
            {
                **base,
                "action": "update",
                "source_id": source_id,
                "content": "Tea towels in drawer",
            },
        )
        with pytest.raises(HomeAssistantError, match="confirmation"):
            await async_manage_knowledge_command(
                hass, {**base, "action": "delete", "source_id": source_id}
            )
        assert await async_manage_knowledge_command(
            hass, {**base, "action": "delete", "source_id": source_id, "confirm": True}
        ) == {"deleted": 1}


async def test_management_api_rejects_invalid_entry_and_subentry() -> None:
    hass = _management_hass()
    hass.config_entries.async_get_entry.return_value = None
    with pytest.raises(HomeAssistantError, match="Integration entry"):
        await async_manage_knowledge_command(
            hass, {"action": "list", "entry_id": "bad", "subentry_id": "bad"}
        )


def test_knowledge_tool_schemas_are_read_only() -> None:
    names = {tool["spec"]["name"] for tool in knowledge_tools()}
    assert names == {"knowledge_search", "knowledge_list", "knowledge_get"}
    assert not names & {"knowledge_create", "knowledge_update", "knowledge_delete"}
