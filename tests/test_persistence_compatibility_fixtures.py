"""Historical and corrupt durable-state fixture contracts."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
    KnowledgeStore,
)
from custom_components.extended_openai_conversation_responses.memory import (
    MemoryStore,
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_WORDING_GROUPS,
    RequestRuleStore,
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager

_FIXTURES = Path(__file__).parent / "fixtures" / "persistence"


def _fixture(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class FixtureStorage:
    """Detached storage that records canonical self-healing writes."""

    def __init__(self, data: Any = None) -> None:
        self.data = deepcopy(data)
        self.save_count = 0

    async def async_load(self) -> Any:
        return deepcopy(self.data)

    async def async_save(self, data: Any) -> None:
        self.data = deepcopy(data)
        self.save_count += 1


class ArchiveFixtureStorage:
    """Partitioned fixture storage matching the Archive persistence boundary."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.metadata = deepcopy(fixture["metadata"])
        self.partitions = deepcopy(fixture["partitions"])

    async def async_load_metadata(self) -> Any:
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data: Any) -> None:
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition: str) -> Any:
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(self, partition: str, data: Any) -> None:
        self.partitions[partition] = deepcopy(data)


async def test_memory_v0_fixture_migrates_with_v2_defaults() -> None:
    """A checked-in pre-v1 list remains readable after the v2 schema upgrade."""
    store = object.__new__(MemoryStore)
    migrated = await store._async_migrate_func(
        0, 0, _fixture("memory_v0_legacy_list.json")
    )
    storage = FixtureStorage(migrated)
    memory = PersistentMemory(storage)

    await memory.async_initialize()

    records = await memory.async_list("alice")
    assert len(records) == 1
    assert records[0].content == "User prefers Celsius."
    assert records[0].importance == "normal"
    assert records[0].subject is None
    assert records[0].key is None
    assert records[0].valid_from is None
    assert records[0].last_confirmed_at is None


async def test_memory_mixed_corruption_self_heals_without_losing_good_record() -> None:
    """Malformed and duplicate records are removed without resetting valid facts."""
    storage = FixtureStorage(_fixture("memory_v2_mixed_corrupt.json"))
    first = PersistentMemory(storage)

    await first.async_initialize()

    records = await first.async_list("alice")
    assert [(record.memory_id, record.key) for record in records] == [
        ("memory-good", "units.temperature")
    ]
    assert storage.save_count == 1
    assert [item["memory_id"] for item in storage.data["memories"]] == [
        "memory-good"
    ]

    second = PersistentMemory(storage)
    await second.async_initialize()
    assert [record.memory_id for record in await second.async_list("alice")] == [
        "memory-good"
    ]
    assert storage.save_count == 1


async def test_knowledge_v1_fixture_migrates_to_explicit_enabled_state() -> None:
    """Legacy Knowledge sources become explicitly enabled without content loss."""
    store = object.__new__(KnowledgeStore)
    migrated = await store._async_migrate_func(
        1, 0, _fixture("knowledge_v1_legacy.json")
    )
    source = migrated["sources"][0]
    assert source["source_id"] == "legacy-source"
    assert source["enabled"] is True

    library = KnowledgeLibrary(FixtureStorage(migrated))
    await library.async_initialize()
    loaded = await library.async_get("legacy-source")
    assert loaded.content == "The heating reference uses Celsius."
    assert loaded.enabled is True


async def test_knowledge_mixed_corruption_preserves_unique_valid_source() -> None:
    """One damaged source cannot make the rest of the Knowledge store unusable."""
    library = KnowledgeLibrary(
        FixtureStorage(_fixture("knowledge_v2_mixed_corrupt.json"))
    )

    await library.async_initialize()

    assert library.total_source_count == 1
    assert library.source_count == 1
    source = await library.async_get("source-good")
    assert source.title == "Heating reference"
    results = await library.async_search("configured temperature unit")
    assert [result.source_id for result in results] == ["source-good"]


async def test_temporary_memory_pre_owner_fixture_preserves_legacy_ownership() -> None:
    """Pre-owner records receive only the safe owner implied by their old scope."""
    memory = TemporaryMemory(
        FixtureStorage(_fixture("temporary_memory_pre_owner.json"))
    )

    await memory.async_initialize()

    alice = await memory.async_list("user:alice", owner_scope_id="user:alice")
    assert [record.memory_id for record in alice] == ["temp-user"]
    assert alice[0].owner_scope_id == "user:alice"
    assert await memory.async_list("user:alice", owner_scope_id="user:bob") == []

    shared = await memory.async_list("shared:household")
    assert [record.memory_id for record in shared] == ["temp-shared"]
    assert shared[0].owner_scope_id is None


async def test_request_rules_v1_fixture_migrates_and_normalizes_once() -> None:
    """Historical storage and superseded routing scope converge canonically."""
    store = object.__new__(RequestRuleStore)
    migrated = await store._async_migrate_func(
        1, 0, _fixture("request_rules_v1_legacy.json")
    )
    assert migrated["wording_groups"] == list(DEFAULT_WORDING_GROUPS)

    storage = FixtureStorage(migrated)
    first = RequestRules(storage)
    await first.async_initialize()
    snapshot = first.snapshot()
    assert snapshot["rules"][0]["action"]["scope"] == "conversation"
    assert storage.save_count == 1

    second = RequestRules(storage)
    await second.async_initialize()
    assert second.snapshot()["rules"][0]["action"]["scope"] == "conversation"
    assert storage.save_count == 1


async def test_archive_mixed_corruption_salvages_valid_session_and_turn() -> None:
    """Malformed neighboring containers cannot block readable retained history."""
    storage = ArchiveFixtureStorage(_fixture("archive_mixed_corrupt.json"))
    archive = ConversationArchive(storage, "agent-1")

    await archive.async_initialize()

    assert archive.stats()["session_count"] == 1
    assert archive.stats()["turn_count"] == 1
    assert archive.active_session("anything") is None
    assert archive._partitions == {"2026-09"}
    result = await archive.async_get("user:alice", "session-good")
    assert [turn["turn_id"] for turn in result["turns"]] == ["turn-good"]


async def test_archive_corrupt_partition_container_does_not_block_metadata() -> None:
    """A damaged monthly partition is isolated from otherwise valid metadata."""
    storage = ArchiveFixtureStorage(_fixture("archive_corrupt_partition.json"))
    archive = ConversationArchive(storage, "agent-1")

    await archive.async_initialize()

    assert archive.stats()["session_count"] == 1
    assert archive.stats()["turn_count"] == 0
    result = await archive.async_get("user:alice", "session-good")
    assert result["turns"] == []


async def test_usage_legacy_flat_totals_fixture_remains_readable() -> None:
    """The original flat aggregate layout remains a supported durable input."""
    fixture = _fixture("usage_compatibility_cases.json")
    manager = UsageManager(FixtureStorage(fixture["legacy_totals"]))

    await manager.async_initialize()

    assert manager.totals.conversation_count == 3
    assert manager.totals.api_request_count == 4
    assert manager.totals.total_tokens == 150
    assert manager.totals.details == {"input_audio_tokens": 2}


async def test_usage_corruption_salvages_valid_totals_and_detail_records() -> None:
    """Bad counter/day fields cannot poison otherwise recoverable usage history."""
    fixture = _fixture("usage_compatibility_cases.json")
    manager = UsageManager(
        FixtureStorage({"conversation_count": 99}),
        FixtureStorage(fixture["daily"]),
        FixtureStorage(fixture["details"]),
        agent_subentry_id="agent-1",
        request_retention_days=10_000,
        run_retention_days=10_000,
    )

    await manager.async_initialize()

    assert manager.totals.conversation_count == 4
    assert manager.totals.api_request_count == 0
    assert manager.totals.successful_request_count == 2
    assert manager.totals.cached_input_tokens == 0
    assert manager.totals.total_tokens == 180
    assert manager.totals.details == {"input_audio_tokens": 3}
    assert manager.daily == {}
    assert [request.request_id for request in manager.requests] == ["request-good"]
    assert [run.run_id for run in manager.runs] == ["run-good"]
