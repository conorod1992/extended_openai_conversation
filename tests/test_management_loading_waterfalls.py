"""Regression tests for management loading waterfall optimizations."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
import custom_components.extended_openai_conversation_responses.management_loading_performance as loading


class _ConfigEntries:
    def __init__(self, entry):
        self.entry = entry

    def async_entries(self, domain):
        assert domain == DOMAIN
        return [self.entry]


def _hass_with_agent():
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Jarvis",
        data=agent_config_defaults(),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        title="Provider",
        data={},
        subentries={subentry.subentry_id: subentry},
    )
    hass = SimpleNamespace(data={}, config_entries=_ConfigEntries(entry))
    return hass


async def test_agent_catalog_does_not_load_scope_dependencies(monkeypatch) -> None:
    """The initial catalogue must stay independent of lazy scope work."""
    hass = _hass_with_agent()
    scope_catalog = AsyncMock(
        side_effect=AssertionError("scope catalogue should be lazy")
    )
    monkeypatch.setattr(management_ui, "_scope_catalog", scope_catalog)

    for name in ("async_get_memory", "async_get_archive"):
        monkeypatch.setattr(
            loading,
            name,
            AsyncMock(side_effect=AssertionError(f"{name} should not be called")),
        )

    result = await loading.async_agent_catalog(hass, "admin", True)

    assert [agent["subentry_id"] for agent in result["agents"]] == ["agent-1"]
    assert result["is_admin"] is True
    scope_catalog.assert_not_awaited()


async def test_scope_catalog_loads_memory_and_archive_concurrently(monkeypatch) -> None:
    """Independent scope managers must start together rather than as a waterfall."""
    memory_started = asyncio.Event()
    archive_started = asyncio.Event()
    memory_counts = {"all": 4, "user": 3}
    archive_counts = {"all": 7, "user": 5}

    async def get_memory(_hass, entry_id, subentry_id):
        assert (entry_id, subentry_id) == ("entry-1", "agent-1")
        memory_started.set()
        await asyncio.wait_for(archive_started.wait(), timeout=1)
        return SimpleNamespace(scope_counts=lambda: memory_counts)

    async def get_archive(_hass, entry_id, subentry_id):
        assert (entry_id, subentry_id) == ("entry-1", "agent-1")
        archive_started.set()
        await asyncio.wait_for(memory_started.wait(), timeout=1)
        return SimpleNamespace(scope_counts=lambda: archive_counts)

    scope_catalog = AsyncMock(return_value=[{"id": "all", "label": "All"}])
    fake_management_ui = SimpleNamespace(
        entry_and_agent=lambda *_args: (object(), object()),
        _scope_catalog=scope_catalog,
    )
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_management_ui)
    monkeypatch.setattr(loading, "async_get_memory", get_memory)
    monkeypatch.setattr(loading, "async_get_archive", get_archive)

    hass = SimpleNamespace()
    result = await loading.async_scope_catalog(
        hass,
        "admin",
        True,
        "entry-1",
        "agent-1",
    )

    assert result == {"scopes": [{"id": "all", "label": "All"}]}
    scope_catalog.assert_awaited_once_with(
        hass,
        "admin",
        True,
        memory_counts,
        archive_counts,
    )
