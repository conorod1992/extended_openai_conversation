"""Focused branch-coverage tests for integration diagnostics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.extended_openai_conversation_responses import (
    diagnostics as diagnostics_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    SUBSYSTEM_STATUS_KEY,
)
from custom_components.extended_openai_conversation_responses.diagnostics import (
    async_get_config_entry_diagnostics,
)


def _stats(**values):
    return SimpleNamespace(stats=lambda: values)


def _entry(*subentries):
    return SimpleNamespace(
        entry_id="entry",
        subentries={subentry.subentry_id: subentry for subentry in subentries},
    )


def _conversation_subentry(*, subentry_id="agent", data=None):
    return SimpleNamespace(
        subentry_type="conversation",
        subentry_id=subentry_id,
        data=data or {},
    )


def _patch_healthy_collectors(monkeypatch):
    guest_mode = SimpleNamespace(status=lambda: {})
    guest_policy = SimpleNamespace(as_diagnostics=lambda: {})
    collectors = SimpleNamespace(
        temporary=AsyncMock(return_value=_stats(temporary_memory_items=1)),
        memory=AsyncMock(return_value=_stats(memory_items=2)),
        knowledge=AsyncMock(return_value=_stats(knowledge_items=3)),
        archive=AsyncMock(return_value=_stats(archive_items=4)),
        usage=AsyncMock(return_value=SimpleNamespace(as_dict=lambda: {"requests": 5})),
        guest=AsyncMock(return_value=guest_mode),
    )
    monkeypatch.setattr(
        diagnostics_module, "async_get_continuity", MagicMock(return_value=_stats())
    )
    monkeypatch.setattr(
        diagnostics_module, "_configured_function_tools", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        diagnostics_module, "validate_function_groups", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        diagnostics_module, "get_function_group_runtime", MagicMock(return_value=None)
    )
    monkeypatch.setattr(diagnostics_module, "async_get_guest_mode", collectors.guest)
    monkeypatch.setattr(
        diagnostics_module, "resolve_guest_policy", MagicMock(return_value=guest_policy)
    )
    monkeypatch.setattr(
        diagnostics_module, "async_get_temporary_memory", collectors.temporary
    )
    monkeypatch.setattr(diagnostics_module, "async_get_memory", collectors.memory)
    monkeypatch.setattr(diagnostics_module, "async_get_knowledge", collectors.knowledge)
    monkeypatch.setattr(diagnostics_module, "async_get_archive", collectors.archive)
    monkeypatch.setattr(diagnostics_module, "async_get_usage", collectors.usage)
    return collectors


async def test_diagnostics_ignores_non_conversation_subentries(hass) -> None:
    """Only conversation subentries should be projected into diagnostics."""
    entry = _entry(
        SimpleNamespace(subentry_type="ai_task", subentry_id="task", data={})
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result == {"conversation_agents": []}


async def test_function_configuration_failure_is_reported_and_collectors_continue(
    hass, monkeypatch
) -> None:
    """Invalid function configuration must not suppress independent diagnostics."""
    collectors = _patch_healthy_collectors(monkeypatch)
    monkeypatch.setattr(
        diagnostics_module,
        "_configured_function_tools",
        MagicMock(side_effect=ValueError("invalid functions")),
    )

    result = await async_get_config_entry_diagnostics(
        hass, _entry(_conversation_subentry())
    )

    agent = result["conversation_agents"][0]
    assert agent["function_group_configuration_error"] == "ValueError"
    assert "guest_mode" not in agent
    assert agent["temporary_memory_items"] == 1
    assert agent["memory_items"] == 2
    assert agent["knowledge_items"] == 3
    assert agent["archive"] == {"archive_items": 4}
    assert agent["usage"] == {"requests": 5}
    collectors.guest.assert_not_awaited()


async def test_failed_optional_stores_are_not_reopened_by_diagnostics(
    hass, monkeypatch
) -> None:
    """Known failed stores should report degradation without another open attempt."""
    collectors = _patch_healthy_collectors(monkeypatch)
    hass.data[SUBSYSTEM_STATUS_KEY] = {
        ("entry", "agent"): {
            "temporary_memory": {"configured": True, "status": "failed"},
            "persistent_memory": {"configured": True, "status": "failed"},
            "archive": {"configured": True, "status": "failed"},
        }
    }

    result = await async_get_config_entry_diagnostics(
        hass, _entry(_conversation_subentry())
    )

    agent = result["conversation_agents"][0]
    assert agent["temporary_memory_storage_error"] == "RuntimeError"
    assert agent["storage_error"] == "RuntimeError"
    assert agent["archive_storage_error"] == "RuntimeError"
    assert agent["knowledge_items"] == 3
    assert agent["usage"] == {"requests": 5}
    collectors.temporary.assert_not_awaited()
    collectors.memory.assert_not_awaited()
    collectors.archive.assert_not_awaited()
    collectors.knowledge.assert_awaited_once()


async def test_usage_failure_is_reported_without_losing_other_diagnostics(
    hass, monkeypatch
) -> None:
    """Usage-store failure should be isolated from the rest of the projection."""
    collectors = _patch_healthy_collectors(monkeypatch)
    collectors.usage.side_effect = OSError("usage unavailable")

    result = await async_get_config_entry_diagnostics(
        hass, _entry(_conversation_subentry())
    )

    agent = result["conversation_agents"][0]
    assert agent["usage_storage_error"] == "OSError"
    assert "usage" not in agent
    assert agent["temporary_memory_items"] == 1
    assert agent["memory_items"] == 2
    assert agent["knowledge_items"] == 3
    assert agent["archive"] == {"archive_items": 4}
