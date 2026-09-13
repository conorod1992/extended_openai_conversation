"""Focused Knowledge Library and startup-isolation regression coverage."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_KNOWLEDGE_ENABLED,
    CONF_TEMPORARY_MEMORY,
    SUBSYSTEM_STATUS_KEY,
)


Agent = conversation_module.ExtendedOpenAIAgentEntity


def _knowledge_agent(
    knowledge: SimpleNamespace, *, allowed_ids: tuple[str, ...] = ("allowed",)
) -> SimpleNamespace:
    policy = SimpleNamespace(
        knowledge_access=True,
        knowledge_source_ids=allowed_ids,
    )
    return SimpleNamespace(
        _knowledge=knowledge,
        _knowledge_available=True,
        _effective_guest_policy=lambda: policy,
    )


@pytest.mark.asyncio
async def test_guest_knowledge_search_intersects_policy_without_leaking_ids() -> None:
    """Forbidden and unknown source IDs must be indistinguishable to a guest."""
    knowledge = SimpleNamespace(async_search=AsyncMock(return_value=[]))

    def resolve_source_filter(source_ids: list[str] | None):
        if source_ids == ["allowed", "secret", "missing"]:
            return {"allowed", "secret"}, ["missing"]
        if source_ids == ["allowed"]:
            return {"allowed"}, []
        raise AssertionError(f"unexpected source filter: {source_ids!r}")

    knowledge.resolve_source_filter = resolve_source_filter
    agent = _knowledge_agent(knowledge)

    result = await Agent._async_execute_knowledge_tool(
        agent,
        "search",
        {
            "query": "boiler",
            "source_ids": ["allowed", "secret", "missing"],
            "limit": 5,
        },
    )

    knowledge.async_search.assert_awaited_once_with("boiler", ["allowed"], 5)
    assert result == {
        "results": [],
        "source_filter": {
            "applied_source_ids": ["allowed"],
            "ignored_source_ids": [],
            "fell_back_to_all_sources": False,
        },
    }


@pytest.mark.asyncio
async def test_guest_knowledge_list_is_scoped_to_policy_sources() -> None:
    """Catalog browsing must receive the guest's source allow-list."""
    knowledge = SimpleNamespace(
        async_catalog=AsyncMock(return_value={"sources": [], "total": 0})
    )
    agent = _knowledge_agent(knowledge)

    result = await Agent._async_execute_knowledge_tool(agent, "list", {})

    knowledge.async_catalog.assert_awaited_once_with(
        None,
        20,
        0,
        ("allowed",),
    )
    assert result == {"sources": [], "total": 0}


@pytest.mark.asyncio
async def test_guest_knowledge_get_rejects_forbidden_source_before_storage_lookup() -> None:
    """A forbidden ID must fail closed without revealing whether it exists."""
    knowledge = SimpleNamespace(async_get_section=AsyncMock())
    agent = _knowledge_agent(knowledge)

    with pytest.raises(RuntimeError) as err:
        await Agent._async_execute_knowledge_tool(
            agent,
            "get",
            {"source_id": "secret"},
        )

    assert str(err.value) == conversation_module.GUEST_MODE_UNAVAILABLE
    knowledge.async_get_section.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_subsystem",
    ["temporary_memory", "archive", "knowledge", "persistent_memory"],
)
async def test_optional_storage_startup_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
    failed_subsystem: str,
) -> None:
    """One optional storage failure must not prevent the other stores from starting."""
    entry = SimpleNamespace(entry_id="entry-id", runtime_data=None)
    subentry = SimpleNamespace(
        subentry_id="subentry-id",
        title="Test agent",
        data={
            CONF_TEMPORARY_MEMORY: "on",
            CONF_ARCHIVE_ENABLED: True,
            CONF_KNOWLEDGE_ENABLED: True,
        },
    )
    agent = Agent(entry, subentry)
    hass = SimpleNamespace(
        data={},
        config=SimpleNamespace(config_dir="/tmp"),
    )
    agent.hass = hass

    monkeypatch.setattr(
        conversation_module.ConversationEntity,
        "async_added_to_hass",
        AsyncMock(),
    )
    monkeypatch.setattr(conversation_module.conversation, "async_set_agent", Mock())
    monkeypatch.setattr(
        conversation_module.SkillManager,
        "async_get_instance",
        AsyncMock(return_value=object()),
    )

    usage = SimpleNamespace(
        request_retention_days=None,
        run_retention_days=None,
        async_prune_details=AsyncMock(),
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_usage",
        AsyncMock(return_value=usage),
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_guest_mode",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_continuity",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        conversation_module,
        "reset_function_group_runtime",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_request_rules",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        conversation_module,
        "get_request_rule_runtime",
        lambda *_args: object(),
    )
    monkeypatch.setattr(conversation_module, "memory_enabled", lambda _data: True)

    archive = SimpleNamespace(async_prune=AsyncMock())
    memory = SimpleNamespace(set_embedding_provider=Mock())
    initializers = {
        "temporary_memory": AsyncMock(return_value=object()),
        "archive": AsyncMock(return_value=archive),
        "knowledge": AsyncMock(return_value=SimpleNamespace()),
        "persistent_memory": AsyncMock(return_value=memory),
    }
    initializers[failed_subsystem].side_effect = RuntimeError(
        f"{failed_subsystem} unavailable"
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_temporary_memory",
        initializers["temporary_memory"],
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_archive",
        initializers["archive"],
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_knowledge",
        initializers["knowledge"],
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_memory",
        initializers["persistent_memory"],
    )

    await Agent.async_added_to_hass(agent)

    for initializer in initializers.values():
        initializer.assert_awaited_once()

    statuses = hass.data[SUBSYSTEM_STATUS_KEY][("entry-id", "subentry-id")]
    assert statuses[failed_subsystem]["status"] == "failed"
    assert statuses[failed_subsystem]["error_type"] == "RuntimeError"
    for subsystem in {
        "temporary_memory",
        "archive",
        "knowledge",
        "persistent_memory",
    } - {failed_subsystem}:
        assert statuses[subsystem]["status"] == "healthy"
