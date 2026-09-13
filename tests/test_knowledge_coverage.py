"""Focused residual coverage for Knowledge Library defensive boundaries."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import knowledge


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
    backing.async_save.assert_awaited_once_with(
        {"sources": [{"source_id": "one"}]}
    )


@pytest.mark.asyncio
async def test_initialize_is_idempotent_and_ignores_non_list_source_payload() -> None:
    library, storage = await _initialized({"sources": "not-a-list"})

    assert library.total_source_count == 0
    await library.async_initialize()
    storage.async_load.assert_awaited_once()


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
    assert "Ignoring Knowledge Library records beyond the per-agent limit" in caplog.text


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
    assert old.source_id not in {item["source_id"] for item in storage.data["sources"]}
    assert storage.data["sources"][0]["source_id"] == "replacement"


@pytest.mark.asyncio
async def test_missing_and_disabled_sources_are_unavailable_to_model_retrieval() -> None:
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
async def test_async_get_knowledge_creates_once_initializes_and_reuses(monkeypatch) -> None:
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


def test_migrated_storage_payload_handles_mapping_invalid_and_non_mapping_inputs() -> None:
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

    assert knowledge._clean_single_line("title", "  Alpha\n\tBeta  ", 20, True) == "Alpha Beta"
    assert knowledge._clean_content(" one  \r\n two\t \rthree  ") == "one\n two\nthree"


def test_token_and_normalization_helpers_drop_single_character_noise() -> None:
    assert knowledge._tokens("A I drill-drill O'Neil x") == {"drill-drill", "o'neil"}
    assert knowledge._normalize("  Drill---PRESS!!  O'Neil  ") == "drill press o'neil"


def test_split_chunks_prefers_nearby_paragraph_boundary_and_overlaps() -> None:
    prefix = "A" * 1700
    content = prefix + "\n\n" + ("B" * 900) + "\n" + ("C" * 900)

    chunks = knowledge._split_chunks(content)

    assert len(chunks) >= 2
    assert chunks[0][0] == 0
    assert chunks[0][1].endswith("A" * 20)
    assert chunks[1][0] < len(chunks[0][1])
    assert all(text for _, text in chunks)


def test_split_chunks_uses_line_boundary_when_paragraph_is_unavailable() -> None:
    content = ("A" * 1750) + "\n" + ("B" * 900)

    chunks = knowledge._split_chunks(content)

    assert len(chunks) >= 2
    assert chunks[0][1] == "A" * 1750
    assert chunks[1][0] <= 1751
