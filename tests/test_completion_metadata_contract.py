"""Completion privacy is enforced on emitted data, including queued snapshots."""

from collections.abc import Mapping
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    completion_events as events,
)

SECRET = "PRIVATE TRANSCRIPT SENTINEL"


@pytest.mark.parametrize("queued", [False, True])
async def test_unknown_fields_cannot_escape_or_fail_committed_action(queued, caplog):
    emitted = []
    hass = SimpleNamespace(
        bus=SimpleNamespace(async_fire=lambda _, payload: emitted.append(dict(payload)))
    )
    backend = []

    async def action():
        backend.append("committed")
        async with events.completion_event_scope() if queued else nullcontext():
            events.queue_completion_event(
                hass, {"response": SECRET, "messages": [SECRET]}
            )
            events.queue_completion_event(
                hass, events.CompletionMetadata("agent", "success", "conversation")
            )
        return "success"

    assert await action() == "success"
    assert backend == ["committed"]
    assert emitted == [
        {
            "schema_version": 2,
            "conversation_id": "conversation",
            "agent_id": "agent",
            "status": "success",
        }
    ]
    assert SECRET not in caplog.text


@pytest.mark.parametrize("queued", [False, True])
async def test_admission_detaches_metadata_before_presentation(queued):
    emitted = []
    hass = SimpleNamespace(
        bus=SimpleNamespace(async_fire=lambda _, payload: emitted.append(dict(payload)))
    )
    metadata = events.CompletionMetadata(
        "agent", "local", "original", True, "LocalIntentError"
    )
    async with events.completion_event_scope() if queued else nullcontext():
        events.queue_completion_event(hass, metadata)
        # Even bypassing the frozen record cannot change an admitted event.
        object.__setattr__(metadata, "conversation_id", SECRET)
    assert emitted == [
        {
            "schema_version": 2,
            "conversation_id": "original",
            "agent_id": "agent",
            "status": "local",
            "handled_locally": True,
            "error_type": "LocalIntentError",
        }
    ]


@pytest.mark.parametrize(
    "field", ["agent_id", "conversation_id", "status", "handled_locally", "error_type"]
)
async def test_invalid_scalar_metadata_is_best_effort_and_private(field, caplog):
    emitted = []
    hass = SimpleNamespace(
        bus=SimpleNamespace(async_fire=lambda _, payload: emitted.append(payload))
    )
    metadata = events.CompletionMetadata("agent", "success")
    object.__setattr__(
        metadata,
        field,
        SECRET if field in {"status", "error_type"} else {"private": SECRET},
    )
    events.queue_completion_event(hass, metadata)
    assert not emitted
    assert SECRET not in caplog.text


async def test_privacy_oracle_detects_restored_arbitrary_payload(monkeypatch, caplog):
    original = events._prepare
    monkeypatch.setattr(
        events,
        "_prepare",
        lambda value: value if isinstance(value, dict) else original(value),
    )
    with pytest.raises(AssertionError):
        await test_unknown_fields_cannot_escape_or_fail_committed_action(True, caplog)


async def test_snapshot_oracle_detects_live_metadata_view(monkeypatch):
    class LiveMetadata(Mapping):
        def __init__(self, value):
            self.value = value

        def __iter__(self):
            return iter(
                (
                    "schema_version",
                    "conversation_id",
                    "agent_id",
                    "status",
                    "handled_locally",
                    "error_type",
                )
            )

        def __len__(self):
            return 6

        def __getitem__(self, key):
            return 2 if key == "schema_version" else getattr(self.value, key)

    monkeypatch.setattr(events, "_prepare", LiveMetadata)
    with pytest.raises(AssertionError):
        await test_admission_detaches_metadata_before_presentation(True)
