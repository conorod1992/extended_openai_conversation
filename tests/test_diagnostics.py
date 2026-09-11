"""Tests for non-sensitive integration diagnostics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.extended_openai_conversation_responses import (
    diagnostics as diagnostics_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_CONF_FUNCTION_TOOLS,
    SUBSYSTEM_STATUS_KEY,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.diagnostics import (
    _configured_function_tools,
    async_get_config_entry_diagnostics,
)


async def test_missing_function_config_uses_execution_default(hass) -> None:
    """Diagnostics must report the tools execution actually falls back to."""
    assert _configured_function_tools({}) == DEFAULT_CONF_FUNCTION_TOOLS


def test_explicit_empty_function_config_remains_empty() -> None:
    """An explicit empty YAML list must not be replaced by defaults."""
    assert _configured_function_tools({"functions": "[]"}) == []


def test_optional_subsystem_runtime_status_distinguishes_all_states(hass) -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.entry = SimpleNamespace(entry_id="entry")
    entity.subentry = SimpleNamespace(subentry_id="agent")

    entity._set_subsystem_status("temporary_memory", False)
    entity._set_subsystem_status("knowledge", True, OSError("unreadable"))
    entity._set_subsystem_status("persistent_memory", True, healthy=True)

    statuses = hass.data[SUBSYSTEM_STATUS_KEY][("entry", "agent")]
    assert statuses["temporary_memory"] == {
        "configured": False,
        "status": "disabled",
    }
    assert statuses["knowledge"] == {
        "configured": True,
        "status": "failed",
        "error_type": "OSError",
    }
    assert statuses["persistent_memory"] == {
        "configured": True,
        "status": "healthy",
    }


async def test_archive_initialization_failure_degrades_and_reports_status(
    hass, caplog
) -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.entry = SimpleNamespace(entry_id="entry")
    entity.subentry = SimpleNamespace(subentry_id="agent", data={})

    with patch(
        "custom_components.extended_openai_conversation_responses.conversation.async_get_archive",
        AsyncMock(side_effect=OSError("unreadable")),
    ):
        await entity._async_initialize_archive(True)

    assert entity._archive is None
    assert hass.data[SUBSYSTEM_STATUS_KEY][("entry", "agent")]["archive"] == {
        "configured": True,
        "status": "failed",
        "error_type": "OSError",
    }
    assert "archive features are unavailable" in caplog.text


def _diagnostics_entry(*, data=None):
    subentry = SimpleNamespace(
        subentry_type="conversation",
        subentry_id="agent",
        data=data or {},
    )
    return SimpleNamespace(entry_id="entry", subentries={"agent": subentry})


def _stats(**values):
    return SimpleNamespace(stats=lambda: values)


async def test_config_entry_diagnostics_projects_populated_runtime_without_secrets(
    hass,
) -> None:
    """The public HA diagnostics endpoint should aggregate safe runtime projections."""
    secret = "CANARY_DIAGNOSTICS_SECRET"
    entry = _diagnostics_entry(data={"api_key": secret})
    hass.data[SUBSYSTEM_STATUS_KEY] = {
        ("entry", "agent"): {
            "temporary_memory": {"configured": True, "status": "healthy"}
        }
    }
    guest_mode = SimpleNamespace(status=lambda: {"enabled": False})
    guest_policy = SimpleNamespace(as_diagnostics=lambda: {"mode": "disabled"})
    usage = SimpleNamespace(as_dict=lambda: {"requests": 7})

    with (
        patch.object(
            diagnostics_module,
            "async_get_continuity",
            return_value=_stats(continuity_records=2),
        ),
        patch.object(diagnostics_module, "_configured_function_tools", return_value=[]),
        patch.object(diagnostics_module, "validate_function_groups", return_value=[]),
        patch.object(
            diagnostics_module,
            "get_function_group_runtime",
            return_value=_stats(function_group_runtime_groups=3),
        ),
        patch.object(
            diagnostics_module,
            "async_get_guest_mode",
            AsyncMock(return_value=guest_mode),
        ),
        patch.object(
            diagnostics_module, "resolve_guest_policy", return_value=guest_policy
        ),
        patch.object(
            diagnostics_module,
            "async_get_temporary_memory",
            AsyncMock(return_value=_stats(temporary_memory_items=4)),
        ),
        patch.object(
            diagnostics_module,
            "async_get_memory",
            AsyncMock(return_value=_stats(memory_items=5)),
        ),
        patch.object(
            diagnostics_module,
            "async_get_knowledge",
            AsyncMock(return_value=_stats(knowledge_items=6)),
        ),
        patch.object(
            diagnostics_module,
            "async_get_archive",
            AsyncMock(return_value=_stats(archive_items=8)),
        ),
        patch.object(
            diagnostics_module,
            "async_get_usage",
            AsyncMock(return_value=usage),
        ),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)

    assert len(result["conversation_agents"]) == 1
    agent = result["conversation_agents"][0]
    assert agent["continuity_records"] == 2
    assert agent["function_group_runtime_groups"] == 3
    assert agent["temporary_memory_items"] == 4
    assert agent["memory_items"] == 5
    assert agent["knowledge_items"] == 6
    assert agent["archive"] == {"archive_items": 8}
    assert agent["usage"] == {"requests": 7}
    assert agent["guest_mode"] == {
        "status": {"enabled": False},
        "policy": {"mode": "disabled"},
    }
    assert secret not in str(result)


async def test_config_entry_diagnostics_isolates_independent_collector_failure(
    hass,
) -> None:
    """One optional collector failure must not hide independent diagnostics."""
    entry = _diagnostics_entry()
    guest_mode = SimpleNamespace(status=lambda: {"enabled": False})
    guest_policy = SimpleNamespace(as_diagnostics=lambda: {})
    usage = SimpleNamespace(as_dict=lambda: {"requests": 9})

    with (
        patch.object(
            diagnostics_module, "async_get_continuity", return_value=_stats()
        ),
        patch.object(diagnostics_module, "_configured_function_tools", return_value=[]),
        patch.object(diagnostics_module, "validate_function_groups", return_value=[]),
        patch.object(diagnostics_module, "get_function_group_runtime", return_value=None),
        patch.object(
            diagnostics_module,
            "async_get_guest_mode",
            AsyncMock(return_value=guest_mode),
        ),
        patch.object(
            diagnostics_module, "resolve_guest_policy", return_value=guest_policy
        ),
        patch.object(
            diagnostics_module,
            "async_get_temporary_memory",
            AsyncMock(return_value=_stats(temporary_memory_items=1)),
        ),
        patch.object(
            diagnostics_module,
            "async_get_memory",
            AsyncMock(side_effect=OSError("memory unavailable")),
        ),
        patch.object(
            diagnostics_module,
            "async_get_knowledge",
            AsyncMock(return_value=_stats(knowledge_items=2)),
        ),
        patch.object(
            diagnostics_module,
            "async_get_archive",
            AsyncMock(return_value=_stats(archive_items=3)),
        ),
        patch.object(
            diagnostics_module,
            "async_get_usage",
            AsyncMock(return_value=usage),
        ),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)

    agent = result["conversation_agents"][0]
    assert agent["storage_error"] == "OSError"
    assert agent["temporary_memory_items"] == 1
    assert agent["knowledge_items"] == 2
    assert agent["archive"] == {"archive_items": 3}
    assert agent["usage"] == {"requests": 9}


async def test_config_entry_diagnostics_reports_failed_optional_subsystem_without_loading(
    hass,
) -> None:
    """A subsystem already marked failed should degrade without re-opening its store."""
    entry = _diagnostics_entry()
    hass.data[SUBSYSTEM_STATUS_KEY] = {
        ("entry", "agent"): {
            "knowledge": {
                "configured": True,
                "status": "failed",
                "error_type": "OSError",
            }
        }
    }
    guest_mode = SimpleNamespace(status=lambda: {})
    guest_policy = SimpleNamespace(as_diagnostics=lambda: {})
    knowledge = AsyncMock(return_value=_stats(knowledge_items=99))

    with (
        patch.object(
            diagnostics_module, "async_get_continuity", return_value=_stats()
        ),
        patch.object(diagnostics_module, "_configured_function_tools", return_value=[]),
        patch.object(diagnostics_module, "validate_function_groups", return_value=[]),
        patch.object(diagnostics_module, "get_function_group_runtime", return_value=None),
        patch.object(
            diagnostics_module,
            "async_get_guest_mode",
            AsyncMock(return_value=guest_mode),
        ),
        patch.object(
            diagnostics_module, "resolve_guest_policy", return_value=guest_policy
        ),
        patch.object(
            diagnostics_module,
            "async_get_temporary_memory",
            AsyncMock(return_value=_stats()),
        ),
        patch.object(
            diagnostics_module, "async_get_memory", AsyncMock(return_value=_stats())
        ),
        patch.object(diagnostics_module, "async_get_knowledge", knowledge),
        patch.object(
            diagnostics_module, "async_get_archive", AsyncMock(return_value=_stats())
        ),
        patch.object(
            diagnostics_module,
            "async_get_usage",
            AsyncMock(return_value=SimpleNamespace(as_dict=lambda: {})),
        ),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)

    agent = result["conversation_agents"][0]
    assert agent["knowledge_storage_error"] == "RuntimeError"
    assert agent["optional_subsystems"]["knowledge"]["status"] == "failed"
    knowledge.assert_not_awaited()
