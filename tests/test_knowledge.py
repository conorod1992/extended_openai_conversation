"""Tests for the on-demand Knowledge Library."""

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from custom_components.extended_openai_conversation_responses import knowledge
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
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
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


async def test_stale_knowledge_editor_cannot_overwrite_newer_source() -> None:
    library = await _library()
    original = await library.async_create("Guide", "", "Original content")
    newer = await library.async_update(
        original.source_id,
        content="Newer content",
        expected_revision=original.updated_at,
    )
    with pytest.raises(ValueError, match="changed in another tab"):
        await library.async_update(
            original.source_id,
            content="Stale content",
            expected_revision=original.updated_at,
        )
    assert (await library.async_get(original.source_id)).content == newer.content


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


async def test_malformed_stored_record_does_not_poison_siblings_or_future_writes(
    caplog,
) -> None:
    valid_a = {
        "source_id": "valid-a",
        "title": "Kitchen storage",
        "description": "Where kitchen items are stored",
        "content": "Tea towels are in the drawer beside the oven.",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "enabled": True,
    }
    corrupt_b = {
        "source_id": "corrupt-b",
        "title": "Corrupt source",
        "description": "This record must not load",
        "content": "This content must never enter the index.",
        "created_at": "2026-01-02T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00",
        "enabled": "true",
    }
    valid_c = {
        "source_id": "valid-c",
        "title": "Garage storage",
        "description": "Where workshop equipment is stored",
        "content": "The masonry drill is on the upper garage shelf.",
        "created_at": "2026-01-03T00:00:00+00:00",
        "updated_at": "2026-01-03T00:00:00+00:00",
        "enabled": True,
    }
    original_payload = {"sources": [valid_a, corrupt_b, valid_c]}
    storage = FakeStorage(original_payload)
    logger_name = "custom_components.extended_openai_conversation_responses.knowledge"
    warning = "Ignoring malformed Knowledge Library record"
    caplog.set_level("WARNING", logger=logger_name)

    library = await _library(storage)

    loaded_ids = {item["source_id"] for item in await library.async_list()}
    assert loaded_ids == {"valid-a", "valid-c"}
    assert "corrupt-b" not in loaded_ids
    assert {
        result.source_id for result in await library.async_search("tea towels")
    } == {"valid-a"}
    assert {
        result.source_id for result in await library.async_search("masonry drill")
    } == {"valid-c"}
    assert await library.async_search("corrupt source") == []
    assert [record.getMessage() for record in caplog.records].count(warning) == 1

    assert storage.data == original_payload

    created = await library.async_create(
        "Bathroom storage",
        "Bathroom supplies",
        "Spare towels are in the airing cupboard.",
    )
    saved_ids = {item["source_id"] for item in storage.data["sources"]}
    assert saved_ids == {"valid-a", "valid-c", created.source_id}
    assert "corrupt-b" not in saved_ids

    caplog.clear()
    reloaded = await _library(storage)
    reloaded_ids = {item["source_id"] for item in await reloaded.async_list()}
    assert reloaded_ids == {"valid-a", "valid-c", created.source_id}
    assert {
        result.source_id for result in await reloaded.async_search("drawer beside oven")
    } == {"valid-a"}
    assert {
        result.source_id for result in await reloaded.async_search("airing cupboard")
    } == {created.source_id}
    assert warning not in [record.getMessage() for record in caplog.records]


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


async def test_search_and_catalog_reuse_precomputed_lexical_features(
    monkeypatch,
) -> None:
    """Repeated reads only tokenize and normalize the query, not indexed source text."""
    library = await _library()
    source = await library.async_create(
        "Concrete wall drill",
        "Masonry tools and fixings",
        "Use the blue masonry bit for concrete walls.",
    )
    features = library._source_features[source.source_id]
    chunk = next(
        item for item in library._chunks.values() if item.source_id == source.source_id
    )

    assert features.title_tokens == frozenset({"concrete", "wall", "drill"})
    assert features.normalized_description == "masonry tools and fixings"
    assert chunk.normalized_text == "use the blue masonry bit for concrete walls"

    original_tokens = knowledge._tokens
    original_normalize = knowledge._normalize
    token_inputs: list[str] = []
    normalize_inputs: list[str] = []

    def tracked_tokens(value: str) -> set[str]:
        token_inputs.append(value)
        return original_tokens(value)

    def tracked_normalize(value: str) -> str:
        normalize_inputs.append(value)
        return original_normalize(value)

    monkeypatch.setattr(knowledge, "_tokens", tracked_tokens)
    monkeypatch.setattr(knowledge, "_normalize", tracked_normalize)

    assert [item.source_id for item in await library.async_search("concrete wall")] == [
        source.source_id
    ]
    catalog = await library.async_catalog("masonry tools")
    assert [item["source_id"] for item in catalog["sources"]] == [source.source_id]

    assert token_inputs == ["concrete wall", "masonry tools"]
    assert normalize_inputs == ["concrete wall", "masonry tools"]


async def test_update_rebuilds_precomputed_lexical_features() -> None:
    """Updating one source replaces its derived metadata and chunk features."""
    library = await _library()
    source = await library.async_create(
        "Old title", "Old description", "Old searchable body"
    )
    old_features = library._source_features[source.source_id]

    await library.async_update(
        source.source_id,
        title="New title",
        description="New description",
        content="Fresh searchable body",
    )

    features = library._source_features[source.source_id]
    chunks = [
        chunk
        for chunk in library._chunks.values()
        if chunk.source_id == source.source_id
    ]
    assert features is not old_features
    assert features.normalized_title == "new title"
    assert features.normalized_description == "new description"
    assert chunks
    assert all("old searchable body" not in chunk.normalized_text for chunk in chunks)
    assert any("fresh searchable body" in chunk.normalized_text for chunk in chunks)


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
    assert await store._async_migrate_func(0, 1, old) == {
        "sources": [{"source_id": "one", "enabled": True}]
    }
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
    base = {"section": "knowledge", "entry_id": "entry-1", "subentry_id": "agent-1"}
    with patch(
        "custom_components.extended_openai_conversation_responses.management_ui.async_get_knowledge",
        AsyncMock(return_value=library),
    ):
        created = await async_management_command(
            hass,
            "admin",
            True,
            {
                **base,
                "action": "create",
                "title": "Kitchen",
                "description": "Layout",
                "content": "Tea towels beside oven",
            },
        )
        source_id = created["source"]["source_id"]
        listed = await async_management_command(
            hass, "admin", True, {**base, "action": "list"}
        )
        assert "content" not in listed["sources"][0]
        assert listed["stats"]["knowledge_source_count"] == 1
        fetched = await async_management_command(
            hass, "admin", True, {**base, "action": "get", "source_id": source_id}
        )
        assert fetched["source"]["content"] == "Tea towels beside oven"
        await async_management_command(
            hass,
            "admin",
            True,
            {
                **base,
                "action": "update",
                "source_id": source_id,
                "content": "Tea towels in drawer",
            },
        )
        with pytest.raises(HomeAssistantError, match="confirmation"):
            await async_management_command(
                hass,
                "admin",
                True,
                {**base, "action": "delete", "source_id": source_id},
            )
        deleted = await async_management_command(
            hass,
            "admin",
            True,
            {**base, "action": "delete", "source_id": source_id, "confirm": True},
        )
        assert deleted["deleted"] == 1
        assert deleted["stats"]["knowledge_source_count"] == 0


async def test_management_api_rejects_invalid_entry_and_subentry() -> None:
    hass = _management_hass()
    hass.config_entries.async_get_entry.return_value = None
    with pytest.raises(HomeAssistantError, match="Integration entry"):
        await async_management_command(
            hass,
            "admin",
            True,
            {
                "section": "knowledge",
                "action": "list",
                "entry_id": "bad",
                "subentry_id": "bad",
            },
        )


def test_knowledge_tool_schemas_are_read_only() -> None:
    names = {tool["spec"]["name"] for tool in knowledge_tools()}
    assert names == {"knowledge_search", "knowledge_list", "knowledge_get"}
    assert not names & {"knowledge_create", "knowledge_update", "knowledge_delete"}


class StorageDouble:
    """Small configurable storage double for residual Knowledge tests."""

    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)
        self.async_load = AsyncMock(side_effect=self._load)
        self.async_save = AsyncMock(side_effect=self._save)

    async def _load(self):
        return deepcopy(self.data)

    async def _save(self, data):
        self.data = deepcopy(data)


def _stored_source(
    source_id: str = "source-1",
    *,
    enabled: bool = True,
    created_at: str = "2026-01-01T00:00:00+00:00",
    updated_at: str = "2026-01-02T00:00:00+00:00",
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "title": f"Title {source_id}",
        "description": "Useful reference material",
        "content": f"Content for {source_id} with searchable details",
        "created_at": created_at,
        "updated_at": updated_at,
        "enabled": enabled,
    }


async def _initialized(data=None) -> tuple[knowledge.KnowledgeLibrary, StorageDouble]:
    storage = StorageDouble(data)
    library = knowledge.KnowledgeLibrary(storage)
    await library.async_initialize()
    return library, storage


@pytest.mark.asyncio
async def test_home_assistant_storage_adapter_builds_private_atomic_store_and_delegates(
    monkeypatch,
) -> None:
    backing = SimpleNamespace(
        async_load=AsyncMock(return_value={"sources": []}),
        async_save=AsyncMock(),
    )
    store_factory = Mock(return_value=backing)
    monkeypatch.setattr(knowledge, "KnowledgeStore", store_factory)
    hass = object()

    storage = knowledge.HomeAssistantKnowledgeStorage(hass, "entry-1", "agent-1")

    store_factory.assert_called_once_with(
        hass,
        knowledge.STORAGE_VERSION,
        f"{knowledge.STORAGE_KEY_PREFIX}.entry-1.agent-1",
        private=True,
        atomic_writes=True,
        serialize_in_event_loop=False,
    )
    assert await storage.async_load() == {"sources": []}
    await storage.async_save({"sources": [{"source_id": "one"}]})
    backing.async_save.assert_awaited_once_with({"sources": [{"source_id": "one"}]})


@pytest.mark.asyncio
async def test_invalid_source_structure_fails_closed_until_repaired() -> None:
    storage = StorageDouble({"sources": "not-a-list"})
    library = KnowledgeLibrary(storage)
    with pytest.raises(ValueError, match="sources have invalid structure"):
        await library.async_initialize()
    assert storage.data == {"sources": "not-a-list"}
    storage.data = {"sources": []}
    await library.async_initialize()
    assert library.total_source_count == 0
    await library.async_initialize()
    assert storage.async_load.await_count == 2


@pytest.mark.asyncio
async def test_initialize_ignores_duplicate_source_ids(caplog) -> None:
    first = _stored_source("duplicate")
    second = {**_stored_source("duplicate"), "title": "Later duplicate"}

    library, _ = await _initialized({"sources": [first, second]})

    assert library.total_source_count == 1
    assert (await library.async_get("duplicate")).title == first["title"]
    assert "Ignoring duplicate Knowledge Library source ID" in caplog.text


@pytest.mark.asyncio
async def test_initialize_stops_at_per_agent_limit(monkeypatch, caplog) -> None:
    monkeypatch.setattr(knowledge, "MAX_SOURCES_PER_AGENT", 1)
    library, _ = await _initialized(
        {"sources": [_stored_source("one"), _stored_source("two")]}
    )

    assert library.total_source_count == 1
    assert (
        "Ignoring Knowledge Library records beyond the per-agent limit" in caplog.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"query": 123}, "query must be a string"),
        ({"limit": True}, "limit must be an integer"),
        ({"limit": 1.5}, "limit must be an integer"),
        ({"offset": False}, "offset must be an integer"),
        ({"offset": "1"}, "offset must be an integer"),
    ],
)
async def test_catalog_rejects_ambiguous_argument_types(kwargs, message) -> None:
    library, _ = await _initialized()

    with pytest.raises(ValueError, match=message):
        await library.async_catalog(**kwargs)


@pytest.mark.asyncio
async def test_catalog_clamps_bounds_and_respects_allowed_source_ids() -> None:
    library, _ = await _initialized()
    first = await library.async_create("Alpha appliance", "Kitchen", "alpha details")
    second = await library.async_create("Beta appliance", "Kitchen", "beta details")

    result = await library.async_catalog(
        query="appliance",
        limit=10_000,
        offset=-50,
        allowed_source_ids=frozenset({second.source_id}),
    )

    assert result["offset"] == 0
    assert result["returned"] == 1
    assert result["sources"][0]["source_id"] == second.source_id
    assert result["sources"][0]["source_id"] != first.source_id


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_limit", [True, 2.5, "2"])
async def test_search_rejects_non_integer_limits(bad_limit) -> None:
    library, _ = await _initialized()

    with pytest.raises(ValueError, match="limit must be an integer"):
        await library.async_search("anything", limit=bad_limit)


@pytest.mark.asyncio
async def test_search_source_filter_skips_disallowed_candidate() -> None:
    library, _ = await _initialized()
    first = await library.async_create("Kitchen", "", "shared searchable token")
    second = await library.async_create("Garage", "", "shared searchable token")

    results = await library.async_search(
        "shared searchable token", source_ids=[second.source_id], limit=10
    )

    assert [result.source_id for result in results] == [second.source_id]
    assert first.source_id not in {result.source_id for result in results}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"start_character": True}, "start_character must be an integer"),
        ({"start_character": 1.5}, "start_character must be an integer"),
        ({"max_characters": False}, "max_characters must be an integer"),
        ({"max_characters": "500"}, "max_characters must be an integer"),
        ({"start_character": -1}, "start_character must be at least 0"),
    ],
)
async def test_get_section_rejects_invalid_paging_arguments(kwargs, message) -> None:
    library, _ = await _initialized()
    source = await library.async_create("Source", "", "source body")

    with pytest.raises(ValueError, match=message):
        await library.async_get_section(source.source_id, **kwargs)


@pytest.mark.asyncio
async def test_get_section_clamps_minimum_and_start_past_end() -> None:
    library, _ = await _initialized()
    source = await library.async_create("Source", "", "x" * 900)

    minimum = await library.async_get_section(source.source_id, 0, 1)
    past_end = await library.async_get_section(source.source_id, 10_000, 500)

    assert minimum["returned_characters"] == 500
    assert minimum["has_more"] is True
    assert past_end["start_character"] == 900
    assert past_end["content"] == ""
    assert past_end["has_more"] is False
    assert past_end["next_start_character"] is None


def test_validate_backup_requires_exact_mapping_shape() -> None:
    for data in (None, [], {}, {"sources": [], "extra": True}):
        with pytest.raises(ValueError, match="incomplete or corrupted"):
            knowledge.KnowledgeLibrary.validate_backup_data(data)


def test_validate_backup_rejects_invalid_count(monkeypatch) -> None:
    with pytest.raises(ValueError, match="source count is invalid"):
        knowledge.KnowledgeLibrary.validate_backup_data({"sources": "bad"})

    monkeypatch.setattr(knowledge, "MAX_SOURCES_PER_AGENT", 1)
    with pytest.raises(ValueError, match="source count is invalid"):
        knowledge.KnowledgeLibrary.validate_backup_data(
            {"sources": [_stored_source("one"), _stored_source("two")]}
        )


def test_validate_backup_rejects_malformed_duplicate_and_invalid_timestamp() -> None:
    with pytest.raises(ValueError, match="source is invalid"):
        knowledge.KnowledgeLibrary.validate_backup_data({"sources": [{"bad": True}]})

    duplicate = _stored_source("duplicate")
    with pytest.raises(ValueError, match="IDs must be unique"):
        knowledge.KnowledgeLibrary.validate_backup_data(
            {"sources": [duplicate, deepcopy(duplicate)]}
        )

    with pytest.raises(ValueError, match="timestamp is invalid"):
        knowledge.KnowledgeLibrary.validate_backup_data(
            {"sources": [_stored_source("bad-time", created_at="not-a-date")]}
        )


@pytest.mark.asyncio
async def test_replace_backup_rebuilds_index_and_persists_canonical_sources() -> None:
    library, storage = await _initialized()
    old = await library.async_create("Old", "", "obsolete searchable value")
    replacement = knowledge.KnowledgeSource(
        source_id="replacement",
        title="Replacement",
        description="new material",
        content="fresh searchable value",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
        enabled=True,
    )

    await library.async_replace_backup([replacement])

    assert await library.async_search("obsolete") == []
    assert [r.source_id for r in await library.async_search("fresh")] == ["replacement"]
    saved_ids = {item["source_id"] for item in storage.data["sources"]}
    assert old.source_id not in saved_ids
    assert storage.data["sources"][0]["source_id"] == "replacement"


@pytest.mark.asyncio
async def test_missing_and_disabled_sources_are_unavailable_to_model_retrieval() -> (
    None
):
    library, _ = await _initialized()
    disabled = await library.async_create("Disabled", "", "private text", enabled=False)

    with pytest.raises(ValueError, match="not found"):
        await library.async_get("missing")
    with pytest.raises(ValueError, match="not found"):
        await library.async_get_section(disabled.source_id)
    assert library.source_count == 0
    assert library.total_source_count == 1


@pytest.mark.asyncio
async def test_unindex_tolerates_missing_token_bucket() -> None:
    library, _ = await _initialized()
    source = await library.async_create("Indexed", "Description", "searchable body")
    assert library._chunks

    token = next(iter(next(iter(library._chunks.values())).tokens))
    library._token_index.pop(token, None)

    library._unindex(source.source_id)

    assert not [key for key in library._chunks if key[0] == source.source_id]


def test_uninitialized_library_rejects_runtime_access() -> None:
    library = knowledge.KnowledgeLibrary(StorageDouble())

    with pytest.raises(RuntimeError, match="has not been initialized"):
        _ = library.source_count


def test_get_loaded_knowledge_reads_existing_manager_without_creating_state() -> None:
    existing = object()
    hass = SimpleNamespace(
        data={knowledge._KNOWLEDGE_MANAGERS: {("entry", "agent"): existing}}
    )

    assert knowledge.get_loaded_knowledge(hass, "entry", "agent") is existing
    assert knowledge.get_loaded_knowledge(hass, "entry", "missing") is None


@pytest.mark.asyncio
async def test_async_get_knowledge_creates_once_initializes_and_reuses(
    monkeypatch,
) -> None:
    storage = object()
    storage_factory = Mock(return_value=storage)
    library = SimpleNamespace(async_initialize=AsyncMock())
    library_factory = Mock(return_value=library)
    monkeypatch.setattr(knowledge, "HomeAssistantKnowledgeStorage", storage_factory)
    monkeypatch.setattr(knowledge, "KnowledgeLibrary", library_factory)
    hass = SimpleNamespace(data={})

    first = await knowledge.async_get_knowledge(hass, "entry", "agent")
    second = await knowledge.async_get_knowledge(hass, "entry", "agent")

    assert first is library
    assert second is library
    storage_factory.assert_called_once_with(hass, "entry", "agent")
    library_factory.assert_called_once_with(storage)
    assert library.async_initialize.await_count == 2


def test_migrated_storage_payload_handles_mapping_invalid_and_non_mapping_inputs() -> (
    None
):
    mapped = knowledge._migrated_storage_payload(
        {"sources": [{"source_id": "one"}, "preserve-malformed"]}
    )
    invalid_sources = knowledge._migrated_storage_payload({"sources": "bad"})
    unrelated = knowledge._migrated_storage_payload(object())

    assert mapped == {
        "sources": [
            {"source_id": "one", "enabled": True},
            "preserve-malformed",
        ]
    }
    assert invalid_sources == {"sources": []}
    assert unrelated == {"sources": []}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("bad", "record must be an object"),
        ({**_stored_source(), "enabled": "yes"}, "enabled must be a boolean"),
        ({**_stored_source(), "title": 123}, "record fields must be strings"),
        ({**_stored_source(), "source_id": ""}, "invalid source ID"),
        ({**_stored_source(), "source_id": "x" * 129}, "invalid source ID"),
    ],
)
def test_source_from_stored_rejects_invalid_record_shapes(raw, message) -> None:
    with pytest.raises(ValueError, match=message):
        knowledge._source_from_stored(raw)


def test_source_from_stored_defaults_legacy_enabled_to_true() -> None:
    raw = _stored_source()
    raw.pop("enabled")

    assert knowledge._source_from_stored(raw).enabled is True


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_validated_enabled_requires_real_boolean(value) -> None:
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        knowledge._validated_enabled(value)


def test_field_cleaners_reject_non_strings_and_normalize_whitespace() -> None:
    with pytest.raises(ValueError, match="title must be a string"):
        knowledge._clean_single_line("title", 123, 10, True)
    with pytest.raises(ValueError, match="content must be a string"):
        knowledge._clean_content(123)

    assert (
        knowledge._clean_single_line("title", "  Alpha\n\tBeta  ", 20, True)
        == "Alpha Beta"
    )
    assert knowledge._clean_content(" one  \r\n two\t \rthree  ") == "one\n two\nthree"


def test_token_and_normalization_helpers_drop_single_character_noise() -> None:
    assert knowledge._tokens("A I drill-drill O'Neil x") == {"drill-drill", "o'neil"}
    assert knowledge._normalize("  Drill---PRESS!!  O'Neil  ") == "drill---press o'neil"


def test_split_chunks_prefers_nearby_paragraph_boundary() -> None:
    prefix = "A" * 1700
    content = prefix + "\n\n" + ("B" * 900) + "\n" + ("C" * 900)

    chunks = knowledge._split_chunks(content)

    assert len(chunks) >= 2
    assert chunks[0][0] == 0
    assert chunks[0][1] == prefix
    assert chunks[1][0] < knowledge.CHUNK_SIZE
    assert all(text for _, text in chunks)


def test_split_chunks_uses_line_boundary_when_paragraph_is_unavailable() -> None:
    content = ("A" * 1750) + "\n" + ("B" * 900)

    chunks = knowledge._split_chunks(content)

    assert len(chunks) >= 2
    assert chunks[0][1] == "A" * 1750
    assert chunks[1][0] <= 1751
