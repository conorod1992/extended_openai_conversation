"""Tests for management frontend bootstrap and network optimizations."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.frontend_version import (
    FRONTEND_VERSION,
)
from custom_components.extended_openai_conversation_responses.management_loading_performance import (
    _agent_snapshot,
    _asset_url,
    _static_paths,
    async_agent_catalog,
    async_overview_summary,
)


class _Auth:
    async def async_get_users(self):
        return [SimpleNamespace(id="admin", name="Admin")]

    async def async_get_user(self, user_id):
        return SimpleNamespace(id=user_id, name="User")


class _ConfigEntries:
    def __init__(self, entry):
        self.entry = entry

    def async_entries(self, domain):
        assert domain == DOMAIN
        return [self.entry]

    def async_get_entry(self, entry_id):
        return self.entry if entry_id == self.entry.entry_id else None


def _hass_with_agent():
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Jarvis",
        data=agent_config_defaults(),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        title="Provider",
        data={},
        subentries={subentry.subentry_id: subentry},
    )
    hass = SimpleNamespace(
        data={},
        auth=_Auth(),
        config_entries=_ConfigEntries(entry),
    )
    return hass, entry, subentry


def test_frontend_asset_version_matches_manifest() -> None:
    manifest = json.loads(
        (
            Path(__file__).parents[1]
            / "custom_components"
            / "extended_openai_conversation_responses"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert FRONTEND_VERSION == manifest["version"]
    assert _asset_url("management-panel.js").endswith(
        f"/assets/{FRONTEND_VERSION}/management-panel.js"
    )


def test_static_paths_keep_legacy_alias_and_cache_versioned_asset() -> None:
    paths = _static_paths(Path("/integration/frontend"), ("management-panel.js",))
    assert len(paths) == 2
    assert paths[0].url_path == f"/{DOMAIN}/management-panel.js"
    assert paths[0].cache_headers is False
    assert paths[1].url_path == _asset_url("management-panel.js")
    assert paths[1].cache_headers is True


def test_agent_snapshot_accepts_frontend_normalized_function_tools() -> None:
    hass, entry, subentry = _hass_with_agent()
    config = agent_config_snapshot(subentry.data)
    assert isinstance(config["functions"], list)

    result = _agent_snapshot(
        hass,
        entry,
        subentry,
        config=config,
        title="Updated Jarvis",
    )

    assert result["title"] == "Updated Jarvis"
    assert result["function_count"] == sum(
        tool.get("enabled", True) is True for tool in config["functions"]
    )


async def test_agent_catalog_does_not_initialize_per_agent_managers(monkeypatch) -> None:
    hass, _entry, _subentry = _hass_with_agent()
    for name in (
        "async_get_usage",
        "async_get_memory",
        "async_get_knowledge",
        "async_get_guest_mode",
    ):
        monkeypatch.setattr(
            "custom_components.extended_openai_conversation_responses."
            f"management_loading_performance.{name}",
            AsyncMock(side_effect=AssertionError(f"{name} should not be called")),
        )

    result = await async_agent_catalog(hass, "admin", True)

    assert [agent["title"] for agent in result["agents"]] == ["Jarvis"]
    assert result["agents"][0]["model"] == agent_config_defaults()["chat_model"]
    assert result["is_admin"] is True


async def test_overview_summary_loads_selected_agent_managers_once(monkeypatch) -> None:
    hass, _entry, _subentry = _hass_with_agent()
    usage = SimpleNamespace(
        as_dict=lambda: {"total_tokens": 200},
        today_summary=lambda: {"total_tokens": 20},
        month_summary=lambda: {"total_tokens": 80},
        latest_run=None,
    )
    memory = SimpleNamespace(stats=lambda: {"memory_count": 7})
    knowledge = SimpleNamespace(source_count=3)
    guest = SimpleNamespace(
        status=lambda: {"state": "scheduled", "currently_active": False}
    )
    mocks = {
        "async_get_usage": AsyncMock(return_value=usage),
        "async_get_memory": AsyncMock(return_value=memory),
        "async_get_knowledge": AsyncMock(return_value=knowledge),
        "async_get_guest_mode": AsyncMock(return_value=guest),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(
            "custom_components.extended_openai_conversation_responses."
            f"management_loading_performance.{name}",
            mock,
        )

    result = await async_overview_summary(
        hass,
        "admin",
        True,
        {"entry_id": "entry-1", "subentry_id": "agent-1"},
    )

    assert result["usage"]["today"]["total_tokens"] == 20
    assert result["agent"]["memory_count"] == 7
    assert result["agent"]["knowledge_source_count"] == 3
    assert result["agent"]["guest_mode"]["state"] == "scheduled"
    assert result["load_errors"] == []
    for mock in mocks.values():
        mock.assert_awaited_once()
