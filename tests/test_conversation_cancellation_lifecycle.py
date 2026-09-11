"""Cancellation lifecycle coverage for the outer conversation pipeline."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
)


class _BlockingResolveContinuity:
    """Continuity double that blocks before ownership is claimed."""

    def __init__(self) -> None:
        self.resolve_started = asyncio.Event()
        self.release_calls: list[tuple[str | None, str | None]] = []

    async def async_resolve(self, *args: Any, **kwargs: Any) -> Any:
        self.resolve_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled resolution unexpectedly resumed")

    async def async_release(
        self, key: str | None, claim_token: str | None
    ) -> None:
        self.release_calls.append((key, claim_token))


class _ClaimedContinuity:
    """Continuity double that records release of a claimed turn."""

    def __init__(self) -> None:
        self.release_calls: list[tuple[str | None, str | None]] = []
        self.release_completed = asyncio.Event()

    async def async_resolve(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            key="continuity-key",
            claim_token="claim-token",
            conversation_id="conversation-id",
            history=None,
        )

    async def async_release(
        self, key: str | None, claim_token: str | None
    ) -> None:
        self.release_calls.append((key, claim_token))
        await asyncio.sleep(0)
        self.release_completed.set()


def _user_input() -> Any:
    """Return the minimum request surface consumed by ``_async_process``."""
    return SimpleNamespace(
        as_llm_context=lambda _domain: SimpleNamespace(
            context=SimpleNamespace(id="context-id")
        ),
        satellite_id=None,
        device_id="device-id",
        conversation_id=None,
    )


def _agent(continuity: Any, process_claimed: Any) -> Any:
    """Return the minimum agent surface consumed by ``_async_process``."""
    return SimpleNamespace(
        subentry=SimpleNamespace(data={}),
        _continuity=continuity,
        _resolve_live_guest_policy=lambda: SimpleNamespace(guest_active=False),
        _async_process_claimed=process_claimed,
    )


async def test_cancellation_during_continuity_resolution_restores_request_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation before a claim propagates without leaking request-local policy."""
    continuity = _BlockingResolveContinuity()

    async def process_claimed(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a cancelled resolution must not enter claimed processing")

    agent = _agent(continuity, process_claimed)
    monkeypatch.setattr(
        conversation_module,
        "resolve_data_scope",
        lambda *args, **kwargs: SimpleNamespace(device_id="device-id"),
    )

    task = asyncio.create_task(
        conversation_module.ExtendedOpenAIAgentEntity._async_process(
            agent, _user_input()
        )
    )
    await continuity.resolve_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert continuity.release_calls == []
    assert conversation_module._ACTIVE_GUEST_POLICY.get() is None


async def test_cancellation_after_continuity_claim_releases_turn_and_restores_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation propagates only after the claimed continuity turn is released."""
    continuity = _ClaimedContinuity()
    processing_started = asyncio.Event()

    async def process_claimed(*args: Any, **kwargs: Any) -> Any:
        processing_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled claimed processing unexpectedly resumed")

    agent = _agent(continuity, process_claimed)
    monkeypatch.setattr(
        conversation_module,
        "resolve_data_scope",
        lambda *args, **kwargs: SimpleNamespace(device_id="device-id"),
    )

    task = asyncio.create_task(
        conversation_module.ExtendedOpenAIAgentEntity._async_process(
            agent, _user_input()
        )
    )
    await processing_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert continuity.release_completed.is_set()
    assert continuity.release_calls == [("continuity-key", "claim-token")]
    assert conversation_module._ACTIVE_GUEST_POLICY.get() is None
