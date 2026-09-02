"""Regression tests for lifecycle and request-path optimizations."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from homeassistant.components import conversation
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import agent_config
from custom_components.extended_openai_conversation_responses.const import (
    CONF_SERVICE_TIER,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.debug import (
    DebugTrace,
)
from custom_components.extended_openai_conversation_responses.lifecycle_optimizations import (
    _LAST_USAGE_PRUNE_DATE,
    _async_prune_usage_if_due,
    install_lifecycle_optimizations,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
    UsageRequest,
    UsageRun,
)


class DelayedStorage:
    """In-memory Store stand-in with Home Assistant delayed-save semantics."""

    def __init__(self) -> None:
        self.data = None
        self.immediate_saves = 0
        self.delayed: list[tuple[object, float]] = []

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.immediate_saves += 1
        self.data = deepcopy(data)

    def async_delay_save(self, data_func, delay: float = 0) -> None:
        self.delayed.append((data_func, delay))

    def latest_delayed_data(self):
        return deepcopy(self.delayed[-1][0]()) if self.delayed else None


async def _usage_manager():
    totals = DelayedStorage()
    daily = DelayedStorage()
    details = DelayedStorage()
    manager = UsageManager(
        totals,
        daily,
        details,
        agent_subentry_id="agent",
    )
    await manager.async_initialize()
    return manager, totals, daily, details


async def test_routine_usage_writes_are_delayed_but_explicit_clear_is_durable() -> None:
    """Ordinary turns leave the response path while explicit maintenance still saves."""
    install_lifecycle_optimizations()
    manager, totals, daily, details = await _usage_manager()

    async with manager.async_run(home_assistant_conversation_id="conversation-a"):
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=5, output_tokens=2, total_tokens=7),
            provider="openai",
            model="gpt-5.6",
            api_mode="responses",
        )

    assert totals.immediate_saves == 0
    assert daily.immediate_saves == 0
    assert details.immediate_saves == 0
    assert totals.delayed
    assert daily.delayed
    assert details.delayed
    assert totals.latest_delayed_data()["total_tokens"] == 7
    assert len(details.latest_delayed_data()["runs"]) == 1

    result = await manager.async_clear_details(confirm=True)
    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert details.immediate_saves == 1
    assert details.data == {"requests": [], "runs": []}


async def test_usage_retention_is_reapplied_lazily_during_long_uptime() -> None:
    """A new UTC day prunes stale detail rows without requiring a restart."""
    install_lifecycle_optimizations()
    manager, _totals, _daily, details = await _usage_manager()
    old = (dt_util.utcnow() - timedelta(days=120)).isoformat()
    manager.requests = [
        UsageRequest(
            request_id="old-request",
            run_id="old-run",
            timestamp=old,
            agent_subentry_id="agent",
            provider="openai",
            model="gpt-5.6",
            api_mode="responses",
            successful=True,
            duration_ms=1,
        )
    ]
    manager.runs = [
        UsageRun(
            run_id="old-run",
            started_at=old,
            completed_at=old,
            duration_ms=1,
            agent_subentry_id="agent",
            home_assistant_conversation_id="old-conversation",
            source_device_id=None,
        )
    ]
    manager.request_retention_days = 30
    manager.run_retention_days = 90
    setattr(manager, _LAST_USAGE_PRUNE_DATE, "1900-01-01")

    await _async_prune_usage_if_due(manager)

    assert manager.requests == []
    assert manager.runs == []
    assert details.delayed
    assert details.latest_delayed_data() == {"requests": [], "runs": []}


async def test_ha_default_never_restores_history_between_distinct_ids() -> None:
    """Prompt-cache reuse must not be confused with Home Assistant chat continuity."""
    manager = ConversationContinuity("agent")
    scope = user_scope("user", source="test")

    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        scope,
        "kitchen",
        "conversation-a",
        30,
    )
    await manager.async_record_success(
        first.key,
        [conversation.SystemContent(content="private first conversation")],
    )
    second = await manager.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        scope,
        "kitchen",
        "conversation-b",
        30,
    )

    assert first.conversation_id == "conversation-a"
    assert second.conversation_id == "conversation-b"
    assert first.key is None and second.key is None
    assert first.history == [] and second.history == []
    assert first.resumed is False and second.resumed is False


def test_default_service_tier_is_interactive_standard_without_overriding_explicit_flex() -> None:
    """Missing service-tier settings use default while explicit flex remains valid."""
    install_lifecycle_optimizations()
    defaults = agent_config.agent_config_defaults()
    assert defaults[CONF_SERVICE_TIER] == "default"

    missing = build_provider_request_snapshot({}, {"api_provider": "openai"})
    explicit = build_provider_request_snapshot(
        {CONF_SERVICE_TIER: "flex"}, {"api_provider": "openai"}
    )
    assert missing.api_kwargs.get("service_tier") == "default"
    assert explicit.api_kwargs.get("service_tier") == "flex"


def test_debug_summary_exposes_mode_without_treating_cache_reuse_as_continuity() -> None:
    install_lifecycle_optimizations()
    trace = DebugTrace(
        debug_id="debug",
        entry_id="entry",
        subentry_id="agent",
        started_at=dt_util.utcnow().isoformat(),
        user_input={},
        incoming_conversation_id="conversation-b",
    )
    trace.continuity = {
        "mode": CONVERSATION_CONTINUITY_HA_DEFAULT,
        "resolved_conversation_id": "conversation-b",
        "resumed": False,
        "restored_history_items": 0,
    }

    summary = trace.summary()

    assert summary["continuity_mode"] == CONVERSATION_CONTINUITY_HA_DEFAULT
    assert summary["restored_history_items"] == 0
