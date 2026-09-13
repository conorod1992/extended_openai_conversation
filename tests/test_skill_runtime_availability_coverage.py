"""Residual coverage for Skill runtime availability wrapper installation."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    skill_runtime_availability,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager


@pytest.fixture(autouse=True)
def restore_runtime_availability_wrappers():
    """Keep the process-wide installer isolated between tests."""
    originals = {
        "tools": ExtendedOpenAIAgentEntity._get_function_tools,
        "loader": ExtendedOpenAIAgentEntity._load_function_groups,
        "preview": management_ui._async_preview_effective_request,
        "installed": skill_runtime_availability._INSTALLED,
    }
    skill_runtime_availability._INSTALLED = False
    try:
        yield
    finally:
        ExtendedOpenAIAgentEntity._get_function_tools = originals["tools"]
        ExtendedOpenAIAgentEntity._load_function_groups = originals["loader"]
        management_ui._async_preview_effective_request = originals["preview"]
        skill_runtime_availability._INSTALLED = originals["installed"]


@pytest.mark.asyncio
async def test_installed_wrappers_use_singleton_fallback_for_live_and_preview(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    def original_tools(entity):
        calls["tools_entity"] = entity
        return [{"result": "tools"}]

    def original_loader(entity, requested):
        calls["loader"] = (entity, requested)
        return {"loaded": list(requested)}

    async def original_preview(hass, entry, subentry, options, user_id):
        calls["preview"] = (hass, entry, subentry, options, user_id)
        return {"preview": True}

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_get_function_tools", original_tools
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_load_function_groups", original_loader
    )
    monkeypatch.setattr(
        management_ui, "_async_preview_effective_request", original_preview
    )

    loaded_manager = object()
    get_loaded = Mock(return_value=loaded_manager)
    monkeypatch.setattr(SkillManager, "get_loaded_instance", get_loaded)

    configured = [{"spec": {"name": "load_skill"}}]
    preview_configured = [{"spec": {"name": "preview_tool"}}]
    monkeypatch.setattr(
        management_ui,
        "configured_function_tools_from_data",
        lambda _options: preview_configured,
    )

    scope_calls: list[tuple[object, object, object]] = []

    @contextmanager
    def fake_scope(options, configured_tools, manager):
        scope_calls.append((options, configured_tools, manager))
        yield

    monkeypatch.setattr(
        skill_runtime_availability, "effective_tool_runtime_scope", fake_scope
    )

    skill_runtime_availability.install_skill_runtime_availability()

    options = {"skills": ["calendar"]}
    entity = SimpleNamespace(
        subentry=SimpleNamespace(data=options),
        skill_manager=object(),
        _get_configured_function_tools=Mock(return_value=configured),
    )

    assert ExtendedOpenAIAgentEntity._get_function_tools(entity) == [
        {"result": "tools"}
    ]
    assert ExtendedOpenAIAgentEntity._load_function_groups(entity, ["skills"]) == {
        "loaded": ["skills"]
    }

    hass = object()
    entry = object()
    subentry = object()
    assert await management_ui._async_preview_effective_request(
        hass, entry, subentry, options, "user-1"
    ) == {"preview": True}

    assert calls["tools_entity"] is entity
    assert calls["loader"] == (entity, ["skills"])
    assert calls["preview"] == (hass, entry, subentry, options, "user-1")
    assert scope_calls == [
        (options, configured, loaded_manager),
        (options, configured, loaded_manager),
        (options, preview_configured, loaded_manager),
    ]
    assert get_loaded.call_count == 3


def test_load_group_wrapper_prefers_entity_skill_manager(hass, monkeypatch) -> None:
    calls: list[tuple[object, object, object]] = []

    def original_tools(_entity):
        return []

    def original_loader(_entity, requested):
        return {"requested": requested}

    async def original_preview(_hass, _entry, _subentry, _options, _user_id):
        return {}

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_get_function_tools", original_tools
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_load_function_groups", original_loader
    )
    monkeypatch.setattr(
        management_ui, "_async_preview_effective_request", original_preview
    )

    singleton = Mock(side_effect=AssertionError("singleton fallback should not run"))
    monkeypatch.setattr(SkillManager, "get_loaded_instance", singleton)

    @contextmanager
    def fake_scope(options, configured_tools, manager):
        calls.append((options, configured_tools, manager))
        yield

    monkeypatch.setattr(
        skill_runtime_availability, "effective_tool_runtime_scope", fake_scope
    )

    manager = SkillManager(hass)
    configured = [{"spec": {"name": "load_skill"}}]
    options = {"skills": ["calendar"]}
    entity = SimpleNamespace(
        subentry=SimpleNamespace(data=options),
        skill_manager=manager,
        _get_configured_function_tools=Mock(return_value=configured),
    )

    skill_runtime_availability.install_skill_runtime_availability()

    assert ExtendedOpenAIAgentEntity._load_function_groups(entity, ["skills"]) == {
        "requested": ["skills"]
    }
    assert calls == [(options, configured, manager)]
    singleton.assert_not_called()


def test_installer_respects_individually_previously_wrapped_callables(
    monkeypatch,
) -> None:
    def marked_tools(_entity):
        return []

    def marked_loader(_entity, _requested):
        return {}

    async def marked_preview(_hass, _entry, _subentry, _options, _user_id):
        return {}

    marked_tools._extended_openai_skill_availability = True
    marked_loader._extended_openai_skill_availability = True
    marked_preview._extended_openai_skill_availability = True

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_get_function_tools", marked_tools
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_load_function_groups", marked_loader
    )
    monkeypatch.setattr(
        management_ui, "_async_preview_effective_request", marked_preview
    )

    skill_runtime_availability.install_skill_runtime_availability()

    assert ExtendedOpenAIAgentEntity._get_function_tools is marked_tools
    assert ExtendedOpenAIAgentEntity._load_function_groups is marked_loader
    assert management_ui._async_preview_effective_request is marked_preview
    assert skill_runtime_availability._INSTALLED is True

    skill_runtime_availability.install_skill_runtime_availability()

    assert ExtendedOpenAIAgentEntity._get_function_tools is marked_tools
    assert ExtendedOpenAIAgentEntity._load_function_groups is marked_loader
    assert management_ui._async_preview_effective_request is marked_preview
