"""Eighth residual coverage sweep for remaining deterministic branches."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
async def test_guest_mode_commit_publish_reconcile_and_invalidate_callbacks(hass, monkeypatch) -> None:
    from pathlib import Path
    import json
    from homeassistant.exceptions import HomeAssistantError
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    await manager.async_initialize()
    observed = []
    manager.async_add_listener(lambda: observed.append(manager.is_active()))
    await manager.async_update_trusted(indefinite=True)
    assert observed == [True]
    write = manager._store._async_write_data

    async def committed_but_unacknowledged(data):
        await write(data)
        raise OSError("directory sync failed after replacement")

    with monkeypatch.context() as fault:
        fault.setattr(manager._store, "_async_write_data", committed_but_unacknowledged)
        with pytest.raises(OSError):
            await manager.async_disable_trusted()
    assert observed == [True, False]
    assert json.loads(Path(manager._store.path).read_text())["data"] == {"schedule": None}
    fresh = guest_mode.GuestModeManager(hass, "entry", "agent")
    await fresh.async_initialize()
    assert not fresh.is_active()

    async def rejected(data):
        raise OSError("write failed")

    with monkeypatch.context() as fault:
        fault.setattr(manager._store, "_async_write_data", rejected)
        fault.setattr(manager._store, "async_load", AsyncMock(side_effect=OSError("readback failed")))
        with pytest.raises(OSError):
            await manager.async_update_trusted(indefinite=True)
    assert manager._persistence_unavailable and not manager._initialized
    with pytest.raises(HomeAssistantError):
        manager.is_active()
    assert observed == [True, False]

    await manager.async_initialize()
    assert observed == [True, False, False]
    await manager.async_update_trusted(indefinite=True)
    assert observed == [True, False, False, True]
    fresh = guest_mode.GuestModeManager(hass, "entry", "agent")
    await fresh.async_initialize()
    assert fresh.is_active()


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

    assert model_tool_results._compact_json_result_content(original, integration_owned=True) is original
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
