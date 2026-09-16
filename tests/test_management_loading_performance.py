"""Tests for management frontend bootstrap and network optimizations."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.frontend_version import (
    FRONTEND_VERSION,
)
import custom_components.extended_openai_conversation_responses.management_loading_performance as loading
from custom_components.extended_openai_conversation_responses.management_loading_performance import (
    _agent_snapshot,
    _asset_url,
    _async_function_repair,
    _async_save_configuration,
    _static_paths,
    async_agent_catalog,
    async_overview_summary,
    async_setup_cached_debug_ui,
    async_setup_cached_management_ui,
)


class _Auth:
    async def async_get_users(self):
        return [SimpleNamespace(id="admin", name="Admin")]

    async def async_get_user(self, user_id):
        return SimpleNamespace(id=user_id, name="User")


class _ConfigEntries:
    def __init__(self, entry):
        self.entry = entry
        self.updates = 0

    def async_entries(self, domain):
        assert domain == DOMAIN
        return [self.entry]

    def async_get_entry(self, entry_id):
        return self.entry if entry_id == self.entry.entry_id else None

    def async_update_subentry(self, entry, subentry, *, data, title=None):
        assert entry is self.entry
        subentry.data = data
        if title is not None:
            subentry.title = title
        self.updates += 1


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


def _persisted_invalid_function_tools() -> str:
    return yaml.safe_dump(
        [
            {
                "spec": {
                    "name": "invalid_phone_tool",
                    "description": "Persisted schema containing malformed supported vocabulary.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "phone": {
                                "type": "string",
                                "enum": ["home", "mobile"],
                                "minLength": "legacy",
                            }
                        },
                    },
                },
                "function": {"type": "native", "name": "execute_service"},
            }
        ],
        sort_keys=False,
    )


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


def test_agent_snapshot_keeps_invalid_function_tool_agent_discoverable() -> None:
    hass, entry, subentry = _hass_with_agent()
    subentry.data["functions"] = _persisted_invalid_function_tools()

    result = _agent_snapshot(hass, entry, subentry)

    assert result["title"] == "Jarvis"
    assert result["function_count"] == 0
    assert result["configuration_issue"]["field"] == "functions"
    assert result["configuration_issue"]["repairable"] is True
    assert "minLength" in result["configuration_issue"]["message"]


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


async def test_agent_catalog_keeps_invalid_function_tool_agent_visible(monkeypatch) -> None:
    hass, _entry, subentry = _hass_with_agent()
    subentry.data["functions"] = _persisted_invalid_function_tools()
    monkeypatch.setattr(management_ui, "_scope_catalog", AsyncMock(return_value=[]))

    result = await async_agent_catalog(hass, "admin", True)

    assert [agent["subentry_id"] for agent in result["agents"]] == ["agent-1"]
    issue = result["agents"][0]["configuration_issue"]
    assert issue["field"] == "functions"
    assert issue["repairable"] is True
    assert "minLength" in issue["message"]


async def test_function_repair_get_exposes_invalid_persisted_tools_without_normalizing() -> None:
    hass, _entry, subentry = _hass_with_agent()
    subentry.data["functions"] = _persisted_invalid_function_tools()

    result = await _async_function_repair(
        hass,
        "admin",
        True,
        {
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "action": "get",
        },
    )

    assert result["tools"][0]["spec"]["parameters"]["properties"]["phone"][
        "minLength"
    ] == "legacy"
    assert "minLength" in result["validation_error"]
    assert isinstance(result["revision"], str)
    assert hass.config_entries.updates == 0


async def test_function_repair_save_is_atomic_and_preserves_unrelated_data() -> None:
    hass, _entry, subentry = _hass_with_agent()
    subentry.data["functions"] = _persisted_invalid_function_tools()
    subentry.data["repair_sentinel"] = {"nested": ["leave", "untouched"]}
    original_sentinel = subentry.data["repair_sentinel"]
    repair = await _async_function_repair(
        hass,
        "admin",
        True,
        {
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "action": "get",
        },
    )

    result = await _async_function_repair(
        hass,
        "admin",
        True,
        {
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "action": "save",
            "revision": repair["revision"],
            "tools": [],
        },
    )

    assert result["valid"] is True
    assert yaml.safe_load(subentry.data["functions"]) == []
    assert subentry.data["repair_sentinel"] is original_sentinel
    assert subentry.data["repair_sentinel"] == {"nested": ["leave", "untouched"]}
    assert hass.config_entries.updates == 1


async def test_function_repair_rejects_still_invalid_tools_without_persisting() -> None:
    hass, _entry, subentry = _hass_with_agent()
    subentry.data["functions"] = _persisted_invalid_function_tools()
    repair = await _async_function_repair(
        hass,
        "admin",
        True,
        {
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "action": "get",
        },
    )
    invalid = yaml.safe_load(_persisted_invalid_function_tools())

    with pytest.raises(Exception, match="minLength"):
        await _async_function_repair(
            hass,
            "admin",
            True,
            {
                "entry_id": "entry-1",
                "subentry_id": "agent-1",
                "action": "save",
                "revision": repair["revision"],
                "tools": invalid,
            },
        )

    assert hass.config_entries.updates == 0
    assert "minLength" in subentry.data["functions"]


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


async def test_configuration_save_normalizes_once(monkeypatch) -> None:
    """The combined Save path validates and persists one normalized candidate."""
    hass, _entry, subentry = _hass_with_agent()
    original_merge = management_ui.merge_agent_config
    merge_calls = 0

    def counted_merge(current, updates):
        nonlocal merge_calls
        merge_calls += 1
        return original_merge(current, updates)

    monkeypatch.setattr(management_ui, "merge_agent_config", counted_merge)
    monkeypatch.setattr(
        management_ui,
        "local_handling_snapshot",
        lambda _hass, _entry, _subentry, _snapshot: {},
    )

    result = await _async_save_configuration(
        hass,
        "admin",
        True,
        {
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "config": {"chat_model": "gpt-5-mini"},
            "title": "Updated Jarvis",
        },
    )

    assert result["valid"] is True
    assert result["config"]["chat_model"] == "gpt-5-mini"
    assert result["title"] == "Updated Jarvis"
    assert subentry.data["chat_model"] == "gpt-5-mini"
    assert hass.config_entries.updates == 1
    assert merge_calls == 1


async def test_configuration_save_validation_failure_does_not_persist(
    monkeypatch,
) -> None:
    """A single failed normalization remains frontend-friendly and write-free."""
    hass, _entry, _subentry = _hass_with_agent()
    original_merge = management_ui.merge_agent_config
    merge_calls = 0

    def counted_merge(current, updates):
        nonlocal merge_calls
        merge_calls += 1
        return original_merge(current, updates)

    monkeypatch.setattr(management_ui, "merge_agent_config", counted_merge)

    result = await _async_save_configuration(
        hass,
        "admin",
        True,
        {
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "config": {
                "speech_regex_replacements": [{"pattern": "[", "replacement": ""}]
            },
        },
    )

    assert result["valid"] is False
    assert "speech_regex_replacements[0].pattern" in result["errors"]
    assert hass.config_entries.updates == 0
    assert merge_calls == 1


async def test_management_setup_retry_resumes_after_panel_failure(monkeypatch) -> None:
    """A failed panel registration must not poison setup or duplicate earlier steps."""
    setup_key = "test.management_ui_setup"
    fake_ui = SimpleNamespace(
        _UI_SETUP=setup_key,
        __file__="/integration/management_ui.py",
        MANAGEMENT_FRONTEND_MODULES=("management-panel.js",),
        websocket_management=object(),
    )
    static_paths = AsyncMock()
    hass = SimpleNamespace(
        data={},
        http=SimpleNamespace(async_register_static_paths=static_paths),
    )
    websocket_register = MagicMock()
    panel_register = AsyncMock(side_effect=[RuntimeError("panel unavailable"), None])
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_ui)
    monkeypatch.setattr(loading.panel_custom, "async_register_panel", panel_register)

    with pytest.raises(RuntimeError, match="panel unavailable"):
        await async_setup_cached_management_ui(hass)

    assert setup_key not in hass.data
    await async_setup_cached_management_ui(hass)

    assert hass.data[setup_key] is True
    assert static_paths.await_count == 1
    assert websocket_register.call_count == 1
    assert panel_register.await_count == 2


async def test_debug_setup_retry_resumes_after_websocket_failure(monkeypatch) -> None:
    """A failed debug websocket registration retries without duplicating static paths."""
    setup_key = "test.debug_ui_setup"
    fake_ui = SimpleNamespace(
        _DEBUG_UI_SETUP=setup_key,
        __file__="/integration/debug_ui.py",
        websocket_request_debug=object(),
    )
    static_paths = AsyncMock()
    hass = SimpleNamespace(
        data={},
        http=SimpleNamespace(async_register_static_paths=static_paths),
    )
    websocket_register = MagicMock(side_effect=[RuntimeError("ws unavailable"), None])
    monkeypatch.setattr(loading, "_debug_ui", lambda: fake_ui)
    monkeypatch.setattr(loading.websocket_api, "async_register_command", websocket_register)

    with pytest.raises(RuntimeError, match="ws unavailable"):
        await async_setup_cached_debug_ui(hass)

    assert setup_key not in hass.data
    await async_setup_cached_debug_ui(hass)

    assert hass.data[setup_key] is True
    assert static_paths.await_count == 1
    assert websocket_register.call_count == 2
