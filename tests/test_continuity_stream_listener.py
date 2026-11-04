"""Request-local listener handoff does not retain callbacks or copy history."""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.continuity import (
    forward_chat_log_deltas,
)
from homeassistant.components import conversation


def _log(text="current question", *, listener=None, role="user"):
    content = (
        conversation.UserContent(content=text)
        if role == "user"
        else conversation.AssistantContent(agent_id="agent", content=text)
    )
    return SimpleNamespace(content=[content], delta_listener=listener)


def test_forward_listener_restores_target_and_leaves_history_untouched():
    listener, previous = object(), object()
    source, target = _log(listener=listener), _log("owned history", listener=previous)
    source_content, target_content = source.content.copy(), target.content.copy()
    with forward_chat_log_deltas(source, target, "current question"):
        assert target.delta_listener is listener
        assert source.delta_listener is listener
        assert source.content == source_content
        assert target.content == target_content
    assert target.delta_listener is previous
    assert source.delta_listener is listener


@pytest.mark.parametrize(
    "case",
    ["no_source", "same_log", "no_listener", "empty", "other_request", "assistant"],
)
def test_forward_listener_ignores_unrelated_or_reused_log(case):
    listener, previous = object(), object()
    source, target = _log(listener=listener), _log(listener=previous)
    if case == "no_source":
        source = None
    elif case == "same_log":
        source = target
    elif case == "no_listener":
        source.delta_listener = None
    elif case == "empty":
        source.content.clear()
    elif case == "other_request":
        source = _log("another question", listener=listener)
    else:
        source = _log(listener=listener, role="assistant")
    with forward_chat_log_deltas(source, target, "current question"):
        assert target.delta_listener is previous
    assert target.delta_listener is previous


def test_forward_listener_restores_target_after_exception():
    listener, previous = object(), object()
    source, target = _log(listener=listener), _log(listener=previous)
    with pytest.raises(RuntimeError, match="provider failed"):
        with forward_chat_log_deltas(source, target, "current question"):
            assert target.delta_listener is listener
            raise RuntimeError("provider failed")
    assert target.delta_listener is previous


async def test_forward_listener_restores_target_after_cancellation():
    listener, previous = object(), object()
    source, target = _log(listener=listener), _log(listener=previous)
    entered = asyncio.Event()

    async def stream():
        with forward_chat_log_deltas(source, target, "current question"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(stream())
    await entered.wait()
    assert target.delta_listener is listener
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert target.delta_listener is previous
    assert source.delta_listener is listener


@pytest.mark.parametrize("with_listener", [False, True])
@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
def test_rerouted_live_admission_never_stays_in_the_source_log(with_listener, failure):
    previous, listener = object(), object() if with_listener else None
    source = _log(listener=listener)
    prefix = [
        conversation.SystemContent(content="owner prompt"),
        conversation.AssistantContent(agent_id="agent", content="owner history"),
    ]
    source.content[:0] = prefix
    source.turn_active = True
    source.turn_start = len(prefix)
    target = _log("guest current input", listener=previous)
    target_content = target.content.copy()

    def run():
        with forward_chat_log_deltas(
            source, target, "current question", rerouted_input=True
        ):
            assert source.content == prefix
            assert target.content == target_content
            assert target.delta_listener is (listener if with_listener else previous)
            if failure:
                raise failure()

    if failure:
        with pytest.raises(failure):
            run()
    else:
        run()
    assert source.content == prefix
    assert target.content == target_content
    assert target.delta_listener is previous


@pytest.mark.parametrize(
    "case", ["unowned_input", "inactive", "already_advanced", "other_text", "same_log"]
)
def test_reroute_cleanup_cannot_remove_an_ancestor_or_advanced_turn(case):
    source, target = _log(listener=object()), _log("target history")
    source.turn_active = case != "inactive"
    source.turn_start = -1 if case == "already_advanced" else 0
    if case == "other_text":
        source.content[0] = conversation.UserContent(content="another request")
    if case == "same_log":
        target = source
    before = source.content.copy()
    with forward_chat_log_deltas(
        source, target, "current question", rerouted_input=case != "unowned_input"
    ):
        assert source.content == before
    assert source.content == before
