"""Tests for safe context truncation strategies."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONTEXT_TRUNCATE_CLEAR,
    CONTEXT_TRUNCATE_KEEP_RECENT,
    CONTEXT_TRUNCATE_SUMMARIZE,
)
from custom_components.extended_openai_conversation_responses.context import (
    keep_recent_messages,
    partition_history,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _convert_content_to_responses_param,
)
from homeassistant.components import conversation


def _normal_history(turns: int = 5) -> list[conversation.Content]:
    content: list[conversation.Content] = [
        conversation.SystemContent(content="System prompt")
    ]
    for number in range(turns):
        content.extend(
            [
                conversation.UserContent(content=f"User turn {number} " * 20),
                conversation.AssistantContent(
                    agent_id="conversation.test",
                    content=f"Assistant turn {number} " * 20,
                ),
            ]
        )
    return content


def test_keep_recent_removes_oldest_complete_normal_turns() -> None:
    """Long conversations retain the system prompt and newest raw context."""
    content = _normal_history()
    assert keep_recent_messages(content, observed_input_tokens=1000, target_tokens=200)

    assert isinstance(content[0], conversation.SystemContent)
    assert "User turn 4" in content[-2].content
    assert all("User turn 0" not in (item.content or "") for item in content)


def test_tool_calls_and_results_are_never_split() -> None:
    """A user turn owns its assistant calls, native items, and tool results."""
    tool = SimpleNamespace(id="call_1", tool_name="turn_on", tool_args={})
    content = [
        conversation.SystemContent(content="System"),
        conversation.UserContent(content="Old request" * 100),
        conversation.AssistantContent(agent_id="conversation.test", tool_calls=[tool]),
        conversation.ToolResultContent(
            agent_id="conversation.test",
            tool_call_id="call_1",
            tool_name="turn_on",
            tool_result={"result": "done"},
        ),
        conversation.AssistantContent(
            agent_id="conversation.test", content="Old answer"
        ),
        conversation.UserContent(content="Recent question"),
        conversation.AssistantContent(
            agent_id="conversation.test", content="Recent answer"
        ),
    ]

    keep_recent_messages(content, observed_input_tokens=1000, target_tokens=100)

    assert all(not isinstance(item, conversation.ToolResultContent) for item in content)
    assert [len(turn) for turn in partition_history(content).turns] == [2]


def test_responses_native_items_remain_valid_when_retained() -> None:
    """Native reasoning and hosted-tool items remain inside their complete turn."""
    native = SimpleNamespace(
        type="reasoning",
        model_dump=lambda **_: {
            "type": "reasoning",
            "id": "rs_1",
            "encrypted_content": "opaque",
        },
    )
    content = _normal_history(2)
    content.insert(
        -1,
        conversation.AssistantContent(agent_id="conversation.test", native=native),
    )
    keep_recent_messages(content, observed_input_tokens=1000, target_tokens=100)

    converted = _convert_content_to_responses_param(content)
    assert any(item.get("type") == "reasoning" for item in converted)


async def test_clear_strategy_keeps_only_system_and_latest_turn() -> None:
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.subentry = SimpleNamespace(
        data={
            CONF_CONTEXT_TRUNCATE_STRATEGY: CONTEXT_TRUNCATE_CLEAR,
            CONF_CONTEXT_THRESHOLD: 100,
        }
    )
    chat_log = SimpleNamespace(content=_normal_history())

    await entity._truncate_message_history(chat_log, observed_input_tokens=1000)

    assert len(partition_history(chat_log.content).turns) == 1


async def test_summarize_strategy_keeps_summary_and_recent_turn() -> None:
    response = SimpleNamespace(output_text="The user chose Celsius.", usage=None)
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=response))
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client)
    entity.subentry = SimpleNamespace(
        data={
            CONF_CONTEXT_TRUNCATE_STRATEGY: CONTEXT_TRUNCATE_SUMMARIZE,
            CONF_CONTEXT_THRESHOLD: 100,
            CONF_CHAT_MODEL: "gpt-5.6-luna",
            CONF_API_MODE: API_MODE_RESPONSES,
        }
    )
    chat_log = SimpleNamespace(content=_normal_history())

    await entity._truncate_message_history(
        chat_log,
        observed_input_tokens=1000,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
    )

    assert isinstance(chat_log.content[1], conversation.SystemContent)
    assert "Celsius" in chat_log.content[1].content
    assert len(partition_history(chat_log.content).turns) >= 1
    client.responses.create.assert_awaited_once()


async def test_summarization_failure_falls_back_to_keep_recent() -> None:
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("offline")))
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client)
    entity.subentry = SimpleNamespace(
        data={
            CONF_CONTEXT_TRUNCATE_STRATEGY: CONTEXT_TRUNCATE_SUMMARIZE,
            CONF_CONTEXT_THRESHOLD: 100,
            CONF_CHAT_MODEL: "gpt-5.6-luna",
            CONF_API_MODE: API_MODE_RESPONSES,
        }
    )
    chat_log = SimpleNamespace(content=_normal_history())

    await entity._truncate_message_history(
        chat_log,
        observed_input_tokens=1000,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
    )

    assert len(partition_history(chat_log.content).turns) == 1
    assert "User turn 4" in chat_log.content[-2].content


def test_keep_recent_is_recommended_new_default() -> None:
    assert CONTEXT_TRUNCATE_KEEP_RECENT == "keep_recent"
