"""Stable unresolved replay state stays separate from invocation bookkeeping."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    backup_transfer,
    delayed_tools,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    _retire_replay_history,
    bind_effect_replay_scope,
    check_effect_replay,
    clear_unacknowledged_calls,
    effect_signature,
    get_agent_replay_safety,
    replay_safety,
)
from homeassistant.exceptions import HomeAssistantError


def owner(hass, entry="entry", assistant="agent"):
    return SimpleNamespace(
        _replay_safety=get_agent_replay_safety(hass, entry, assistant)
    )


@pytest.mark.parametrize("global_retirement", [False, True])
def test_retirement_and_acknowledgement_survive_replacement(hass, global_retirement):
    first = owner(hass)
    _retire_replay_history(first, None if global_retirement else "conversation")
    replacement = owner(hass)
    with (
        bind_effect_replay_scope(replacement, "conversation"),
        pytest.raises(HomeAssistantError, match="not acknowledged"),
    ):
        check_effect_replay(replacement, "anything")
    clear_unacknowledged_calls(replacement, "conversation")
    with bind_effect_replay_scope(first, "conversation"):
        check_effect_replay(first, "anything")
    assert not hasattr(replacement, "_dispatched_tool_inputs")


def test_agent_and_conversation_ownership_are_exact(hass):
    first = owner(hass)
    signature = effect_signature("file_write", {"path": "target", "content": "done"})
    replay_safety(first).effects["conversation"] = {signature}
    for other, conversation_id in [
        (owner(hass, assistant="sibling"), "conversation"),
        (owner(hass, entry="other"), "conversation"),
        (owner(hass), "new"),
    ]:
        with bind_effect_replay_scope(other, conversation_id):
            check_effect_replay(other, signature)
    replacement = owner(hass)
    with (
        bind_effect_replay_scope(replacement, "conversation"),
        pytest.raises(HomeAssistantError),
    ):
        check_effect_replay(replacement, signature)


async def test_deletion_removes_only_matching_safety_owner(hass, monkeypatch):
    removed, sibling, other = (
        owner(hass),
        owner(hass, assistant="sibling"),
        owner(hass, entry="other"),
    )
    monkeypatch.setattr(delayed_tools, "async_remove_stored_agent_calls", AsyncMock())
    monkeypatch.setattr(backup_transfer, "async_remove_agent_transfers", AsyncMock())
    await agent_deletion.async_delete_agent_data(hass, "entry", "agent")
    owners = hass.data["extended_openai_conversation_responses.replay_safety_states"]
    assert ("entry", "agent") not in owners
    assert owners[("entry", "sibling")] is replay_safety(sibling)
    assert owners[("other", "agent")] is replay_safety(other)
    # An already-bound old generation cannot recreate the deleted registry entry.
    _retire_replay_history(removed, "late")
    assert ("entry", "agent") not in owners
