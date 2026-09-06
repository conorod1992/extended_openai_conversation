"""Regression tests for legacy AI Task migration parity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.extended_openai_conversation_responses import (
    async_migrate_integration,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONFIG_ENTRY_VERSION,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_AI_TASK_OPTIONS,
    DOMAIN,
)


class FakeConfigEntries:
    """Small mutable config-entry manager for structural migration tests."""

    def __init__(self, entries: list[SimpleNamespace]) -> None:
        self.entries = entries
        self.added_subentries = []

    def async_entries(self, domain: str):
        assert domain == DOMAIN
        return self.entries

    def async_add_subentry(self, entry, subentry) -> None:
        self.added_subentries.append((entry, subentry))
        entry.subentries[subentry.subentry_id] = subentry

    def async_update_entry(self, entry, **changes) -> None:
        for key, value in changes.items():
            setattr(entry, key, value)

    def async_update_subentry(self, _entry, _subentry, **_changes) -> None:
        # These tests exercise structural migration. Per-conversation option
        # normalization is covered by the existing migration/config tests.
        return None


def _entry(
    *,
    version: int,
    subentries: dict | None = None,
    options: dict | None = None,
    entry_id: str = "entry",
):
    return SimpleNamespace(
        entry_id=entry_id,
        title="Legacy agent",
        version=version,
        disabled_by=None,
        options={} if options is None else options,
        subentries={} if subentries is None else dict(subentries),
    )


def _subentry(subentry_type: str, *, subentry_id: str):
    return SimpleNamespace(
        subentry_id=subentry_id,
        subentry_type=subentry_type,
        title=subentry_type,
        unique_id=None,
        data={},
    )


async def test_v1_migration_adds_conversation_and_default_ai_task() -> None:
    """A genuine v1 install gains the same two default subentry types as fresh setup."""
    legacy_options = {"chat_model": "gpt-4o-mini"}
    entry = _entry(version=1, options=legacy_options)
    manager = FakeConfigEntries([entry])
    hass = MagicMock()
    hass.config_entries = manager

    await async_migrate_integration(hass)

    assert entry.version == CONFIG_ENTRY_VERSION
    assert entry.options == {}
    types = [subentry.subentry_type for subentry in entry.subentries.values()]
    assert types.count("conversation") == 1
    assert types.count("ai_task_data") == 1

    conversation_subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == "conversation"
    )
    ai_task_subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == "ai_task_data"
    )
    assert dict(conversation_subentry.data) == legacy_options
    assert ai_task_subentry.title == DEFAULT_AI_TASK_NAME
    assert dict(ai_task_subentry.data) == DEFAULT_AI_TASK_OPTIONS
    assert ai_task_subentry.data is not DEFAULT_AI_TASK_OPTIONS


async def test_v1_migration_is_idempotent_after_partial_subentry_creation() -> None:
    """Retrying an interrupted v1 migration does not duplicate either subentry type."""
    existing_conversation = _subentry("conversation", subentry_id="conversation-existing")
    existing_ai_task = _subentry("ai_task_data", subentry_id="ai-task-existing")
    entry = _entry(
        version=1,
        subentries={
            existing_conversation.subentry_id: existing_conversation,
            existing_ai_task.subentry_id: existing_ai_task,
        },
    )
    manager = FakeConfigEntries([entry])
    hass = MagicMock()
    hass.config_entries = manager

    await async_migrate_integration(hass)

    assert entry.version == CONFIG_ENTRY_VERSION
    assert manager.added_subentries == []
    types = [item.subentry_type for item in entry.subentries.values()]
    assert types.count("conversation") == 1
    assert types.count("ai_task_data") == 1


async def test_modern_entry_without_ai_task_is_not_repopulated() -> None:
    """A deliberately removed AI Task on a modern entry stays removed."""
    modern_conversation = _subentry("conversation", subentry_id="modern-conversation")
    modern = _entry(
        version=CONFIG_ENTRY_VERSION,
        subentries={modern_conversation.subentry_id: modern_conversation},
        entry_id="modern",
    )
    legacy = _entry(version=1, entry_id="legacy")
    manager = FakeConfigEntries([modern, legacy])
    hass = MagicMock()
    hass.config_entries = manager

    await async_migrate_integration(hass)

    assert modern.version == CONFIG_ENTRY_VERSION
    assert all(
        subentry.subentry_type != "ai_task_data"
        for subentry in modern.subentries.values()
    )
    modern_additions = [
        subentry for target, subentry in manager.added_subentries if target is modern
    ]
    assert modern_additions == []
