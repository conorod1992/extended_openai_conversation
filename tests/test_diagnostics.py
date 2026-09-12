"""Tests for non-sensitive integration diagnostics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
    diagnostics as diagnostics_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_EMBEDDING_MODEL,
    CONF_MEMORY_MODE,
    CONF_MEMORY_RETRIEVAL_MODE,
    CONF_TEMPORARY_MEMORY,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_CONF_FUNCTION_TOOLS,
    MEMORY_MODE_MANUAL,
    MEMORY_RETRIEVAL_HYBRID,
    SUBSYSTEM_STATUS_KEY,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.diagnostics import (
    _configured_function_tools,
    async_get_config_entry_diagnostics,
)

_ORIGINAL_AGENT_ADDED_TO_HASS = ExtendedOpenAIAgentEntity.async_added_to_hass


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


async def test_conversation_platform_adds_only_conversation_subentries(
    monkeypatch,
) -> None:
    conversation_subentry = SimpleNamespace(
        subentry_type="conversation", subentry_id="agent"
    )
    other_subentry = SimpleNamespace(subentry_type="ai_task", subentry_id="task")
    entry = SimpleNamespace(
        subentries={"task": other_subentry, "agent": conversation_subentry}
    )
    created_entity = object()
    constructor = MagicMock(return_value=created_entity)
    add_entities = MagicMock()
    monkeypatch.setattr(
        conversation_module, "ExtendedOpenAIAgentEntity", constructor
    )

    await conversation_module.async_setup_entry(object(), entry, add_entities)

    constructor.assert_called_once_with(entry, conversation_subentry)
    add_entities.assert_called_once_with(
        [created_entity], config_subentry_id="agent"
    )


def _startup_entity(hass, data: dict) -> ExtendedOpenAIAgentEntity:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.entry = SimpleNamespace(entry_id="entry")
    entity.subentry = SimpleNamespace(subentry_id="agent", data=data)
    return entity


def _patch_startup_dependencies(
    monkeypatch,
    *,
    temporary_getter: AsyncMock | None = None,
    knowledge_getter: AsyncMock | None = None,
    memory_getter: AsyncMock | None = None,
) -> SimpleNamespace:
    dependencies = SimpleNamespace(
        usage=SimpleNamespace(async_prune_details=AsyncMock()),
        guest_mode=object(),
        continuity=object(),
        function_groups=object(),
        request_rules=object(),
        request_rule_runtime=object(),
        temporary_memory=object(),
        archive=SimpleNamespace(async_prune=AsyncMock()),
        knowledge=object(),
        memory=SimpleNamespace(set_embedding_provider=MagicMock()),
        skill_manager=object(),
        base_added=AsyncMock(),
        set_agent=MagicMock(),
    )
    dependencies.temporary_getter = temporary_getter or AsyncMock(
        return_value=dependencies.temporary_memory
    )
    dependencies.knowledge_getter = knowledge_getter or AsyncMock(
        return_value=dependencies.knowledge
    )
    dependencies.memory_getter = memory_getter or AsyncMock(
        return_value=dependencies.memory
    )
    patches = {
        "async_get_usage": AsyncMock(return_value=dependencies.usage),
        "async_get_guest_mode": AsyncMock(return_value=dependencies.guest_mode),
        "async_get_continuity": MagicMock(return_value=dependencies.continuity),
        "reset_function_group_runtime": MagicMock(
            return_value=dependencies.function_groups
        ),
        "async_get_request_rules": AsyncMock(return_value=dependencies.request_rules),
        "get_request_rule_runtime": MagicMock(
            return_value=dependencies.request_rule_runtime
        ),
        "async_get_temporary_memory": dependencies.temporary_getter,
        "async_get_archive": AsyncMock(return_value=dependencies.archive),
        "async_get_knowledge": dependencies.knowledge_getter,
        "async_get_memory": dependencies.memory_getter,
    }
    monkeypatch.setattr(
        conversation_module.ConversationEntity,
        "async_added_to_hass",
        dependencies.base_added,
    )
    monkeypatch.setattr(
        conversation_module.conversation,
        "async_set_agent",
        dependencies.set_agent,
    )
    monkeypatch.setattr(
        conversation_module.SkillManager,
        "async_get_instance",
        AsyncMock(return_value=dependencies.skill_manager),
    )
    for name, value in patches.items():
        monkeypatch.setattr(conversation_module, name, value)
    return dependencies


async def test_agent_startup_initializes_enabled_conversation_subsystems(
    hass, monkeypatch
) -> None:
    """Startup wires enabled stores and applies their operational settings."""
    data = {
        CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        CONF_ARCHIVE_ENABLED: True,
        CONF_ARCHIVE_RETENTION_DAYS: 21,
        CONF_KNOWLEDGE_ENABLED: True,
        CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
        CONF_MEMORY_RETRIEVAL_MODE: MEMORY_RETRIEVAL_HYBRID,
        CONF_MEMORY_EMBEDDING_MODEL: "text-embedding-test",
        CONF_USAGE_REQUEST_RETENTION_DAYS: 14,
        CONF_USAGE_RUN_RETENTION_DAYS: 45,
    }
    entity = _startup_entity(hass, data)
    dependencies = _patch_startup_dependencies(monkeypatch)

    await _ORIGINAL_AGENT_ADDED_TO_HASS(entity)

    dependencies.base_added.assert_awaited_once()
    dependencies.set_agent.assert_called_once_with(hass, entity.entry, entity)
    assert entity.skill_manager is dependencies.skill_manager
    assert entity._usage is dependencies.usage
    assert entity._guest_mode is dependencies.guest_mode
    assert entity._continuity is dependencies.continuity
    assert entity._function_groups_runtime is dependencies.function_groups
    assert entity._request_rules is dependencies.request_rules
    assert entity._request_rule_runtime is dependencies.request_rule_runtime
    assert entity._temporary_memory is dependencies.temporary_memory
    assert entity._archive is dependencies.archive
    assert entity._knowledge is dependencies.knowledge
    assert entity._memory is dependencies.memory
    assert dependencies.usage.request_retention_days == 14
    assert dependencies.usage.run_retention_days == 45
    dependencies.usage.async_prune_details.assert_awaited_once_with()
    dependencies.archive.async_prune.assert_awaited_once_with(21)
    dependencies.memory.set_embedding_provider.assert_called_once_with(
        entity._async_create_embeddings, "text-embedding-test"
    )
    statuses = hass.data[SUBSYSTEM_STATUS_KEY][("entry", "agent")]
    assert statuses == {
        "temporary_memory": {"configured": True, "status": "healthy"},
        "archive": {"configured": True, "status": "healthy"},
        "knowledge": {"configured": True, "status": "healthy"},
        "persistent_memory": {"configured": True, "status": "healthy"},
    }


async def test_agent_startup_isolates_independent_optional_store_failures(
    hass, monkeypatch, caplog
) -> None:
    """One unavailable optional store must not prevent the others being attempted."""
    entity = _startup_entity(
        hass,
        {
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
        },
    )
    temporary_getter = AsyncMock(side_effect=OSError("temporary unavailable"))
    knowledge_getter = AsyncMock(side_effect=ValueError("knowledge corrupt"))
    memory_getter = AsyncMock(side_effect=RuntimeError("memory unavailable"))
    _patch_startup_dependencies(
        monkeypatch,
        temporary_getter=temporary_getter,
        knowledge_getter=knowledge_getter,
        memory_getter=memory_getter,
    )

    await _ORIGINAL_AGENT_ADDED_TO_HASS(entity)

    temporary_getter.assert_awaited_once()
    knowledge_getter.assert_awaited_once()
    memory_getter.assert_awaited_once()
    assert entity._temporary_memory is None
    assert entity._knowledge is None
    assert entity._memory is None
    statuses = hass.data[SUBSYSTEM_STATUS_KEY][("entry", "agent")]
    assert statuses["temporary_memory"] == {
        "configured": True,
        "status": "failed",
        "error_type": "OSError",
    }
    assert statuses["knowledge"] == {
        "configured": True,
        "status": "failed",
        "error_type": "ValueError",
    }
    assert statuses["persistent_memory"] == {
        "configured": True,
        "status": "failed",
        "error_type": "RuntimeError",
    }
    assert "Unable to initialize temporary memory" in caplog.text
    assert "Unable to initialize Knowledge Library" in caplog.text
    assert "Unable to initialize persistent memory" in caplog.text


async def test_agent_startup_keeps_disabled_optional_stores_unopened(
    hass, monkeypatch
) -> None:
    """Default startup avoids retention stores while keeping knowledge readable."""
    entity = _startup_entity(hass, {})
    dependencies = _patch_startup_dependencies(monkeypatch)

    await _ORIGINAL_AGENT_ADDED_TO_HASS(entity)

    dependencies.temporary_getter.assert_not_awaited()
    dependencies.memory_getter.assert_not_awaited()
    assert entity._temporary_memory is None
    assert entity._memory is None
    assert entity._knowledge is dependencies.knowledge
    statuses = hass.data[SUBSYSTEM_STATUS_KEY][("entry", "agent")]
    assert statuses["temporary_memory"]["status"] == "disabled"
    assert statuses["archive"]["status"] == "disabled"
    assert statuses["knowledge"] == {"configured": False, "status": "disabled"}
    assert statuses["persistent_memory"]["status"] == "disabled"


async def test_agent_removal_unregisters_all_conversation_runtime_state(
    hass, monkeypatch
) -> None:
    entity = _startup_entity(hass, {})
    unset_agent = MagicMock()
    remove_runtime = MagicMock()
    base_removed = AsyncMock()
    monkeypatch.setattr(
        conversation_module.conversation, "async_unset_agent", unset_agent
    )
    monkeypatch.setattr(
        conversation_module, "remove_function_group_runtime", remove_runtime
    )
    monkeypatch.setattr(
        conversation_module.ConversationEntity,
        "async_will_remove_from_hass",
        base_removed,
    )

    await entity.async_will_remove_from_hass()

    unset_agent.assert_called_once_with(hass, entity.entry)
    remove_runtime.assert_called_once_with(hass, "entry", "agent")
    base_removed.assert_awaited_once()


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
