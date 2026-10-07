"""Curated plausible dirty-state corpus from long-lived EOAI installations."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import PersistentMemory
from custom_components.extended_openai_conversation_responses.request_rules import (
    STORAGE_VERSION as RULES_VERSION,
    RequestRuleStore,
    RequestRules,
)
from tests_real_ha.test_cross_feature_acceptance import _rule
from tests_stress.conftest import record


class _MemoryStorage:
    def __init__(self, payload):
        self.payload = deepcopy(payload)
        self.saved = []

    async def async_load(self):
        return deepcopy(self.payload)

    async def async_save(self, data):
        self.payload = deepcopy(data)
        self.saved.append(deepcopy(data))


class _KnowledgeStorage:
    def __init__(self, payload):
        self.payload = deepcopy(payload)

    async def async_load(self):
        return deepcopy(self.payload)

    async def async_save(self, data):
        self.payload = deepcopy(data)


def _memory(memory_id, user_id, content, *, key=None, **extra):
    base = {
        "memory_id": memory_id,
        "user_id": user_id,
        "content": content,
        "category": "preferences",
        "source": "explicit",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "subject": None,
        "key": key,
        "valid_from": None,
    }
    base.update(extra)
    return base


def _source(source_id, title, **extra):
    base = {
        "source_id": source_id,
        "title": title,
        "description": "Long-lived user reference",
        "content": f"{title} durable content",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "enabled": True,
    }
    base.update(extra)
    return base


async def test_dirty_memory_corpus_preserves_valid_and_orphaned_records() -> None:
    """Obsolete fields and bad siblings cannot erase unrelated durable facts."""
    storage = _MemoryStorage(
        {
            "memories": [
                _memory(
                    "valid",
                    "current-user",
                    "Keep the temperature in Celsius",
                    importance=0.8,
                    last_confirmed_at="2025-12-01T00:00:00+00:00",
                    embedding=[0.1, 0.2],
                ),
                # Deleted HA users may legitimately remain in historical private data.
                _memory("orphan", "deleted-user-id", "Old owner durable preference"),
                _memory("valid", "current-user", "Duplicate ID must lose"),
                _memory(
                    "duplicate-key",
                    "current-user",
                    "Duplicate key must lose",
                    key="home.units",
                ),
                _memory(
                    "first-key",
                    "current-user",
                    "Canonical keyed preference",
                    key="home.units",
                ),
                {"memory_id": "malformed", "content": 123},
                "not-a-record",
            ]
        }
    )
    memory = PersistentMemory(storage)
    await memory.async_initialize()

    current = await memory.async_list("current-user", limit=100)
    orphan = await memory.async_list("deleted-user-id", limit=100)
    assert {item.memory_id for item in current} == {"valid", "duplicate-key"}
    assert [item.memory_id for item in orphan] == ["orphan"]
    assert storage.saved, "dirty memory state should be rewritten to a canonical form"
    healed = storage.saved[-1]["memories"]
    assert {item["memory_id"] for item in healed} == {"valid", "orphan", "duplicate-key"}
    assert not any(
        key in item for item in healed for key in ("importance", "last_confirmed_at", "embedding")
    )


async def test_dirty_knowledge_corpus_ignores_bad_and_duplicate_siblings() -> None:
    """A malformed or duplicated source must not prevent healthy sources loading."""
    storage = _KnowledgeStorage(
        {
            "sources": [
                _source("healthy", "Healthy reference"),
                _source("healthy", "Duplicate loses"),
                _source("disabled", "Disabled reference", enabled=False),
                _source("bad-enabled", "Malformed enabled", enabled="yes"),
                {"source_id": "missing-fields"},
                None,
            ]
        }
    )
    library = KnowledgeLibrary(storage)
    await library.async_initialize()

    listed = await library.async_list()
    assert {item["source_id"] for item in listed} == {"healthy", "disabled"}
    assert library.source_count == 1
    assert library.total_source_count == 2
    assert (await library.async_get("healthy")).content == "Healthy reference durable content"


@pytest.mark.usefixtures("real_store_io")
async def test_dirty_request_rule_corpus_self_heals_without_losing_opaque_fields(
    hass, stress_trace
) -> None:
    """Mixed legacy, duplicate and invalid Rules converge to one safe generation."""
    key = "extended_openai_conversation_responses.request_rules.dirty.agent"
    store = RequestRuleStore(hass, RULES_VERSION, key)

    legacy = _rule(
        "model_routing",
        {
            "model": "gpt-5-mini",
            "reasoning_effort": "medium",
            "scope": "request",
            "reset": False,
            # Historical complete routing records omitted continue_to_ai.
        },
        phrase="legacy complete route",
    )
    legacy.update({"id": "legacy-route", "name": "Legacy complete route", "order": 0})
    duplicate = deepcopy(legacy)
    duplicate["name"] = "Duplicate must lose"
    invalid = {"id": "broken", "name": "Broken", "phrases": 4}

    await store.async_save(
        {
            "future_metadata": {"source": "older-dev-build", "kept": True},
            "rules": [legacy, duplicate, invalid],
        }
    )

    manager = RequestRules(store)
    await manager.async_initialize()
    snapshot = manager.snapshot()
    assert [rule["id"] for rule in snapshot["rules"]] == ["legacy-route"]
    # A complete routing rule from the old request-scope semantics must retain its
    # effect by migrating to conversation scope.
    assert snapshot["rules"][0]["action"]["scope"] == "conversation"

    durable = await store.async_load()
    assert durable["future_metadata"] == {
        "source": "older-dev-build",
        "kept": True,
    }
    assert [rule["id"] for rule in durable["rules"]] == ["legacy-route"]
    assert durable["rules"][0]["action"]["scope"] == "conversation"
    record(
        stress_trace,
        "dirty_legacy_state_corpus",
        memories=True,
        knowledge=True,
        request_rules=True,
        opaque_fields_preserved=True,
    )
