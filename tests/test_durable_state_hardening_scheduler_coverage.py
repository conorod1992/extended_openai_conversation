"""Retention scheduler coverage for durable state hardening."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    durable_state_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)


@pytest.mark.asyncio
async def test_archive_retention_schedule_registers_daily_callback_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    scheduled: list[Any] = []
    removals: list[Any] = []

    async def original_added(agent: Any) -> None:
        calls.append(("original", agent))

    async def retention(agent: Any, now: Any = None) -> None:
        calls.append(("retention", agent, now))

    def track(hass: Any, callback: Any, interval: Any) -> str:
        scheduled.append((hass, callback, interval))
        return "unsubscribe"

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "async_added_to_hass", original_added
    )
    monkeypatch.setattr(hardening, "async_track_time_interval", track)
    monkeypatch.setattr(hardening, "async_prune_archive_retention", retention)

    hardening._install_archive_retention_schedule()
    installed = ExtendedOpenAIAgentEntity.async_added_to_hass
    hardening._install_archive_retention_schedule()
    assert ExtendedOpenAIAgentEntity.async_added_to_hass is installed

    agent = SimpleNamespace(
        hass="hass",
        async_on_remove=lambda value: removals.append(value),
    )
    await installed(agent)

    assert calls == [("original", agent)]
    assert removals == ["unsubscribe"]
    assert len(scheduled) == 1
    assert scheduled[0][0] == "hass"
    assert scheduled[0][2] == hardening._ARCHIVE_RETENTION_INTERVAL

    marker = object()
    await scheduled[0][1](marker)
    assert calls[-1] == ("retention", agent, marker)
