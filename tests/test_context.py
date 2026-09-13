"""Tests for conversation-history truncation helpers."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.components import conversation

from custom_components.extended_openai_conversation_responses import context


def test_partition_history_with_only_prefix_content() -> None:
    """History without a user turn remains entirely in the prefix."""
    system = conversation.SystemContent(content="System prompt")
    assistant = conversation.AssistantContent(agent_id="agent", content="Hello")

    parts = context.partition_history([system, assistant])

    assert parts.prefix == [system, assistant]
    assert parts.turns == []


def test_partition_history_keeps_tool_sequence_in_user_turn() -> None:
    """Assistant tool calls and results stay attached to their originating turn."""
    system = conversation.SystemContent(content="System prompt")
    user_one = conversation.UserContent(content="Turn one")
    assistant_tool = conversation.AssistantContent(
        agent_id="agent",
        tool_calls=[
            SimpleNamespace(
                tool_name="get_state",
                tool_args={"entity_id": "light.kitchen"},
            )
        ],
    )
    tool_result = conversation.ToolResultContent(
        agent_id="agent",
        tool_call_id="call-1",
        tool_name="get_state",
        tool_result={"state": "on"},
    )
    user_two = conversation.UserContent(content="Turn two")
    assistant_two = conversation.AssistantContent(
        agent_id="agent", content="Second response"
    )

    parts = context.partition_history(
        [system, user_one, assistant_tool, tool_result, user_two, assistant_two]
    )

    assert parts.prefix == [system]
    assert parts.turns == [
        [user_one, assistant_tool, tool_result],
        [user_two, assistant_two],
    ]


def test_weight_falls_back_when_content_cannot_serialize() -> None:
    """Weighting tolerates content whose as_dict implementation fails."""

    class BrokenContent:
        def as_dict(self):
            raise TypeError("not serializable")

        def __str__(self) -> str:
            return "fallback-content"

    assert context._weight([BrokenContent()]) > 0


def test_retained_turns_returns_single_turn_unchanged() -> None:
    """A single turn is never removed, even when above the target."""
    turn = [conversation.UserContent(content="Only turn")]
    parts = context.HistoryParts(prefix=[], turns=[turn])

    assert context.retained_turns(parts, 10_000, 1) == [turn]


def test_keep_recent_messages_preserves_complete_newest_turn() -> None:
    """Truncation drops an old tool sequence atomically instead of splitting it."""
    system = conversation.SystemContent(content="System prompt")
    old_user = conversation.UserContent(content="Old turn " * 20)
    old_assistant = conversation.AssistantContent(
        agent_id="agent",
        tool_calls=[SimpleNamespace(tool_name="old_tool", tool_args={"value": 1})],
    )
    old_result = conversation.ToolResultContent(
        agent_id="agent",
        tool_call_id="old-call",
        tool_name="old_tool",
        tool_result={"result": "old"},
    )
    new_user = conversation.UserContent(content="New turn")
    new_assistant = conversation.AssistantContent(
        agent_id="agent", content="New response"
    )
    history = [
        system,
        old_user,
        old_assistant,
        old_result,
        new_user,
        new_assistant,
    ]

    changed = context.keep_recent_messages(history, 10_000, 1)

    assert changed is True
    assert history == [system, new_user, new_assistant]


def test_keep_recent_messages_reports_no_change_for_single_turn() -> None:
    """History is left untouched when no complete old turn can be removed."""
    system = conversation.SystemContent(content="System prompt")
    user = conversation.UserContent(content="Only turn")
    assistant = conversation.AssistantContent(agent_id="agent", content="Reply")
    history = [system, user, assistant]

    assert context.keep_recent_messages(history, 10_000, 1) is False
    assert history == [system, user, assistant]


def test_select_summary_history_requires_multiple_turns() -> None:
    """There is nothing to summarize when only one user turn exists."""
    history = [
        conversation.SystemContent(content="System prompt"),
        conversation.UserContent(content="Only turn"),
        conversation.AssistantContent(agent_id="agent", content="Reply"),
    ]

    assert context.select_summary_history(history, 100, 50) is None


def test_select_summary_history_forces_one_raw_recent_turn() -> None:
    """A generous budget still leaves older turns for summary generation."""
    system = conversation.SystemContent(content="System prompt")
    user_one = conversation.UserContent(content="One")
    assistant_one = conversation.AssistantContent(agent_id="agent", content="A")
    user_two = conversation.UserContent(content="Two")
    assistant_two = conversation.AssistantContent(agent_id="agent", content="B")
    user_three = conversation.UserContent(content="Three")
    assistant_three = conversation.AssistantContent(agent_id="agent", content="C")
    history = [
        system,
        user_one,
        assistant_one,
        user_two,
        assistant_two,
        user_three,
        assistant_three,
    ]

    selected = context.select_summary_history(history, 1, 10_000)

    assert selected is not None
    older, recent = selected
    assert older == [user_one, assistant_one, user_two, assistant_two]
    assert recent == [system, user_three, assistant_three]


def test_history_as_summary_text_renders_tool_calls_and_results() -> None:
    """Tool activity is retained as inert, human-readable summary input."""
    assistant = conversation.AssistantContent(
        agent_id="agent",
        tool_calls=[
            SimpleNamespace(
                tool_name="get_state",
                tool_args={"entity_id": "light.kitchen"},
            )
        ],
    )
    result = conversation.ToolResultContent(
        agent_id="agent",
        tool_call_id="call-1",
        tool_name="get_state",
        tool_result={"state": "on"},
    )

    rendered = context.history_as_summary_text([assistant, result])

    assert (
        'assistant tool call get_state: {"entity_id": "light.kitchen"}' in rendered
    )
    assert 'tool result get_state: {"state": "on"}' in rendered


def test_history_as_summary_text_renders_native_model_dump_without_content() -> None:
    """Native provider items are serialized when no text content is available."""

    class NativeItem:
        def model_dump(self, *, exclude_none: bool):
            assert exclude_none is True
            return {"type": "reasoning", "value": "native"}

    assistant = conversation.AssistantContent(
        agent_id="agent",
        content=None,
        native=NativeItem(),
    )

    rendered = context.history_as_summary_text([assistant])

    assert rendered == (
        'assistant native item: {"type": "reasoning", "value": "native"}'
    )


def test_history_as_summary_text_renders_plain_native_value() -> None:
    """Native values without model_dump are still serialized safely."""
    assistant = conversation.AssistantContent(
        agent_id="agent",
        content=None,
        native={"type": "custom", "value": 42},
    )

    assert context.history_as_summary_text([assistant]) == (
        'assistant native item: {"type": "custom", "value": 42}'
    )
