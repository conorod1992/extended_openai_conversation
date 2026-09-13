"""Focused coverage for conversation lifecycle edge paths."""

from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import conversation_lifecycle


@pytest.mark.parametrize(
    ("state_session_id", "memory_session_id"),
    [(None, "memory-1"), ("state-1", None)],
)
def test_request_fresh_conversation_rejects_missing_active_session_ids(
    state_session_id: str | None,
    memory_session_id: str | None,
) -> None:
    """An active lifecycle still requires both exact session identifiers."""
    token = conversation_lifecycle.begin_conversation_lifecycle()
    try:
        with pytest.raises(
            RuntimeError, match="No active conversation is available to reset"
        ):
            conversation_lifecycle.request_fresh_conversation(
                state_session_id, memory_session_id
            )
        assert conversation_lifecycle.requested_conversation_reset() is None
    finally:
        conversation_lifecycle.end_conversation_lifecycle(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("state_session_id", ["opaque-session", "conversation:"])
async def test_reset_without_conversation_id_skips_ignore(
    monkeypatch: pytest.MonkeyPatch,
    state_session_id: str,
) -> None:
    """Only a non-empty HA conversation id should be ignored after reset."""
    continuity = Mock()
    continuity.async_request_end = AsyncMock()
    continuity.async_ignore_next_incoming_conversation_id = AsyncMock()
    continuity.async_clear_memory_bundle = AsyncMock()

    request_rules = Mock()
    monkeypatch.setattr(
        conversation_lifecycle, "get_function_group_runtime", lambda *_args: None
    )
    monkeypatch.setattr(
        conversation_lifecycle, "get_request_rule_runtime", lambda *_args: request_rules
    )

    hass = Mock()
    await conversation_lifecycle.async_reset_conversation_context(
        hass,
        continuity,
        "entry-1",
        "subentry-1",
        continuity_key=None,
        state_session_id=state_session_id,
        memory_session_id="memory-1",
    )

    continuity.async_request_end.assert_not_awaited()
    continuity.async_ignore_next_incoming_conversation_id.assert_not_awaited()
    continuity.async_clear_memory_bundle.assert_awaited_once_with("memory-1")
    request_rules.reset.assert_called_once_with(state_session_id)
