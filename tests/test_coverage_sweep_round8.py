"""Eighth residual coverage sweep for remaining deterministic branches."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
async def test_guest_mode_commit_publish_reconcile_and_invalidate_callbacks(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    schedule = guest_mode.GuestModeSchedule(
        active_from="2026-10-05T18:00:00+00:00",
        active_until=None,
        source="home_assistant",
        updated_at="2026-10-05T18:00:00+00:00",
    )
    listener = Mock()
    manager._listeners.add(listener)
    manager._store = SimpleNamespace(
        async_save=Mock(return_value=object()),
        async_load=AsyncMock(return_value={"schedule": None}),
    )

    async def settle(_op, _rollback, publish, reconcile, invalidate):
        publish()
        assert manager._schedule == schedule
        await reconcile()
        assert manager._schedule is None
        invalidate()

    monkeypatch.setattr(
        guest_mode,
        "_async_settle_transactional_save",
        settle,
    )

    await manager._async_commit_schedule(schedule)

    assert manager._persistence_unavailable is True
    assert manager._initialized is False
    assert listener.call_count == 2


def test_backup_preview_forget_only_removes_matching_session(hass) -> None:
    from custom_components.extended_openai_conversation_responses import backup_transfer

    session = backup_transfer.ImportSession(
        session_id="one",
        entry_id="entry",
        subentry_id="agent",
        path="/tmp/one",
        filename="one.json",
        expected_size=1,
        kind="legacy_json",
        expires_at=999999999999,
    )
    previews = backup_transfer._latest_previews(hass)
    key = ("entry", "agent")

    previews[key] = ("other", "token")
    backup_transfer._forget_preview(hass, session)
    assert previews[key] == ("other", "token")

    previews[key] = ("one", "token")
    backup_transfer._forget_preview(hass, session)
    assert key not in previews


@pytest.mark.asyncio
async def test_model_catalog_candidate_skips_dynamic_and_reset_routing_rules(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_catalog_manager as manager_module,
    )

    options = {manager_module.CONF_CHAT_MODEL: manager_module.DEFAULT_CHAT_MODEL}
    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        data=options,
    )
    entry = SimpleNamespace(
        entry_id="entry",
        data={},
        subentries={"agent": subentry},
    )
    hass.config_entries.async_entries = Mock(return_value=[entry])

    rules = SimpleNamespace(
        snapshot=Mock(
            return_value={
                "rules": [
                    {"action_type": "local_action", "action": {}},
                    {"action_type": "model_routing", "action": {"reset": True}},
                    {
                        "action_type": "model_routing",
                        "action": {"model": "{model}", "reasoning_effort": None},
                    },
                    {
                        "action_type": "model_routing",
                        "action": {"model": None, "reasoning_effort": "{effort}"},
                    },
                    {
                        "action_type": "model_routing",
                        "action": {"model": None, "reasoning_effort": None},
                    },
                ]
            }
        )
    )
    monkeypatch.setattr(
        manager_module,
        "async_get_request_rules",
        AsyncMock(return_value=rules),
    )
    build = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request.build_provider_request_snapshot",
        build,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request.conversation_tools_required",
        Mock(return_value=False),
    )

    manager = manager_module.ModelCatalogManager(hass)

    assert (
        await manager._candidate_preserves_saved_requests(
            manager_module.BUNDLED_CATALOG
        )
        is True
    )
    assert build.call_count == 1


@pytest.mark.asyncio
async def test_model_catalog_candidate_rejects_concrete_routed_request(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_catalog_manager as manager_module,
    )

    options = {manager_module.CONF_CHAT_MODEL: manager_module.DEFAULT_CHAT_MODEL}
    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        data=options,
    )
    entry = SimpleNamespace(
        entry_id="entry",
        data={},
        subentries={"agent": subentry},
    )
    hass.config_entries.async_entries = Mock(return_value=[entry])
    rules = SimpleNamespace(
        snapshot=Mock(
            return_value={
                "rules": [
                    {
                        "action_type": "model_routing",
                        "action": {
                            "model": manager_module.DEFAULT_CHAT_MODEL,
                            "reasoning_effort": "medium",
                        },
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(
        manager_module,
        "async_get_request_rules",
        AsyncMock(return_value=rules),
    )

    calls = 0

    def build(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("routed request invalid")
        return SimpleNamespace()

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request.build_provider_request_snapshot",
        build,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request.conversation_tools_required",
        Mock(return_value=False),
    )

    manager = manager_module.ModelCatalogManager(hass)

    assert (
        await manager._candidate_preserves_saved_requests(
            manager_module.BUNDLED_CATALOG
        )
        is False
    )
    assert calls == 2


@pytest.mark.asyncio
async def test_request_rules_instrumented_match_seam_returns_non_rule_match(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules
    from tests.test_request_rules import MemoryStore

    manager = request_rules.RequestRules(MemoryStore())
    manager._committed_matching_snapshot = request_rules._MatchingSnapshot(
        phrases=(),
        wording_groups=(),
        deterministic=(
            (
                {"id": "one", "name": "One", "match_type": "equals", "order": 0},
                {
                    "word_forms": False,
                    "wording_alternatives": False,
                    "fuzzy_threshold": 90,
                },
                request_rules.CompiledPhrase("hello", normalized="hello"),
            ),
        ),
        fuzzy=(),
    )
    sentinel = object()
    monkeypatch.setattr(manager, "match", Mock(return_value=sentinel))
    hass.async_add_executor_job = AsyncMock(return_value=sentinel)

    match, skipped = await manager.async_match_with_skipped(hass, "hello")

    assert match is sentinel
    assert skipped == []


@pytest.mark.asyncio
async def test_request_rules_instrumented_match_seam_returns_none(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules
    from tests.test_request_rules import MemoryStore

    manager = request_rules.RequestRules(MemoryStore())
    manager._committed_matching_snapshot = request_rules._MatchingSnapshot(
        phrases=(),
        wording_groups=(),
        deterministic=(
            (
                {"id": "one", "name": "One", "match_type": "equals", "order": 0},
                {
                    "word_forms": False,
                    "wording_alternatives": False,
                    "fuzzy_threshold": 90,
                },
                request_rules.CompiledPhrase("hello", normalized="hello"),
            ),
        ),
        fuzzy=(),
    )
    monkeypatch.setattr(manager, "match", Mock(return_value=None))
    hass.async_add_executor_job = AsyncMock(return_value=None)

    match, skipped = await manager.async_match_with_skipped(hass, "hello")

    assert match is None
    assert skipped == []


def test_model_tool_result_nondefault_source_filter_is_preserved() -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_tool_results,
    )

    result = {
        "source_filter": {
            "applied_source_ids": ["source"],
            "ignored_source_ids": [],
            "fell_back_to_all_sources": False,
        }
    }

    assert (
        model_tool_results.knowledge_search_payload(
            result,
            filter_requested=False,
            policy_filter_applied=False,
        )
        is result
    )


def test_model_tool_result_compact_json_mutates_mapping_only_when_needed(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_tool_results,
    )

    original = object()
    data = {"result": '{ "a": 1 }'}
    monkeypatch.setattr(
        model_tool_results,
        "tool_result_data",
        Mock(return_value=data),
    )

    assert model_tool_results._compact_json_result_content(original) is original
    assert data["result"] == '{"a":1}'


def test_guest_mode_status_scheduled_active_expired_and_indefinite(hass) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    now = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)

    manager._schedule = guest_mode.GuestModeSchedule(
        active_from="2026-10-05T20:00:00+00:00",
        active_until="2026-10-05T21:00:00+00:00",
        source="home_assistant",
        updated_at="2026-10-05T18:00:00+00:00",
    )
    assert manager.status(now)["state"] == "scheduled"

    manager._schedule = guest_mode.GuestModeSchedule(
        active_from="2026-10-05T18:00:00+00:00",
        active_until="2026-10-05T20:00:00+00:00",
        source="home_assistant",
        updated_at="2026-10-05T18:00:00+00:00",
    )
    assert manager.status(now)["state"] == "active"

    manager._schedule = guest_mode.GuestModeSchedule(
        active_from="2026-10-05T18:00:00+00:00",
        active_until="2026-10-05T18:30:00+00:00",
        source="home_assistant",
        updated_at="2026-10-05T18:00:00+00:00",
    )
    assert manager.status(now)["state"] == "inactive"

    manager._schedule = guest_mode.GuestModeSchedule(
        active_from="2026-10-05T18:00:00+00:00",
        active_until=None,
        source="home_assistant",
        updated_at="2026-10-05T18:00:00+00:00",
    )
    assert manager.status(now)["state"] == "active_indefinitely"
