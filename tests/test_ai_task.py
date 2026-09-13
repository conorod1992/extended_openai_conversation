"""Tests for the Extended OpenAI AI Task platform."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import OpenAIError

from homeassistant.components import ai_task, conversation
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import ai_task as ai_task_platform


def _subentry(
    subentry_id: str = "task-1", *, subentry_type: str = "ai_task_data"
) -> SimpleNamespace:
    return SimpleNamespace(
        subentry_id=subentry_id,
        subentry_type=subentry_type,
        title="AI Task",
        data={},
    )


def _entity() -> ai_task_platform.ExtendedOpenAITaskEntity:
    return ai_task_platform.ExtendedOpenAITaskEntity(
        cast(Any, SimpleNamespace(data={}, runtime_data=object())), cast(Any, _subentry())
    )


@pytest.mark.asyncio
async def test_setup_entry_adds_only_ai_task_subentries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []
    added: list[tuple[list[object], str]] = []

    def fake_entity(_entry: object, subentry: SimpleNamespace) -> object:
        created.append(subentry.subentry_id)
        return SimpleNamespace(subentry_id=subentry.subentry_id)

    monkeypatch.setattr(ai_task_platform, "ExtendedOpenAITaskEntity", fake_entity)
    entry = SimpleNamespace(
        subentries={
            "conversation": _subentry("conversation", subentry_type="conversation"),
            "task": _subentry("task"),
        }
    )

    await ai_task_platform.async_setup_entry(
        cast(Any, SimpleNamespace()),
        cast(Any, entry),
        lambda entities, *, config_subentry_id: added.append(
            (list(entities), config_subentry_id)
        ),
    )

    assert created == ["task"]
    assert len(added) == 1
    assert added[0][1] == "task"
    assert getattr(added[0][0][0], "subentry_id") == "task"


def test_entity_advertises_generate_data_and_attachment_features() -> None:
    entity = _entity()

    assert entity.unique_id == "task-1"
    assert entity.supported_features == (
        ai_task.AITaskEntityFeature.GENERATE_DATA
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
    )


@pytest.mark.asyncio
async def test_generate_plain_data_without_llm_api_uses_empty_tool_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity = _entity()
    handled: dict[str, Any] = {}

    async def fake_handle(_chat_log: object, **kwargs: Any) -> None:
        handled.update(kwargs)

    monkeypatch.setattr(entity, "_async_handle_chat_log", fake_handle)
    chat_log = SimpleNamespace(
        llm_api=None,
        content=[conversation.AssistantContent(agent_id="agent", content="plain text")],
        conversation_id="conversation-1",
    )
    task = SimpleNamespace(name="plain", structure=None)

    result = await entity._async_generate_data(cast(Any, task), cast(Any, chat_log))

    assert handled["function_tools"] == []
    assert handled["exposed_entities"] == []
    assert handled["llm_context"] is None
    assert handled["structure_name"] == "plain"
    assert result.conversation_id == "conversation-1"
    assert result.data == "plain text"


@pytest.mark.asyncio
async def test_generate_structured_data_preserves_caller_api_and_serializer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity = _entity()
    llm_context = object()
    caller_api = SimpleNamespace(llm_context=llm_context)
    snapshot = object()
    tools = [{"spec": {"name": "caller_tool"}}]
    provided: dict[str, Any] = {}
    handled: dict[str, Any] = {}

    monkeypatch.setattr(
        ai_task_platform,
        "caller_api_tools",
        lambda api: (snapshot, tools) if api is caller_api else AssertionError(),
    )

    async def fake_provide_llm_data(**kwargs: Any) -> None:
        provided.update(kwargs)
        chat_log.llm_api = None

    async def fake_handle(_chat_log: object, **kwargs: Any) -> None:
        handled.update(kwargs)
        chat_log.content.append(
            conversation.AssistantContent(
                agent_id="agent", content='{"answer": "ok"}'
            )
        )

    chat_log = SimpleNamespace(
        llm_api=caller_api,
        content=[],
        conversation_id="conversation-2",
        async_provide_llm_data=fake_provide_llm_data,
    )
    monkeypatch.setattr(entity, "_async_handle_chat_log", fake_handle)
    monkeypatch.setattr(
        ai_task_platform,
        "parse_ai_task_structured_response",
        lambda text: {"parsed": text},
    )
    monkeypatch.setattr(
        ai_task_platform,
        "tool_snapshot_scope",
        lambda current: pytest.MonkeyPatch.context()
        if current is snapshot
        else AssertionError(),
    )

    task = SimpleNamespace(name="structured", structure={"type": "object"})
    result = await entity._async_generate_data(cast(Any, task), cast(Any, chat_log))

    assert provided["llm_context"] is llm_context
    assert provided["user_llm_prompt"] == ai_task_platform.DEFAULT_SYSTEM_PROMPT
    assert chat_log.llm_api is caller_api
    assert handled["function_tools"] is tools
    assert handled["llm_context"] is llm_context
    assert handled["structure_name"] == "structured"
    assert handled["structure"] == {"type": "object"}
    assert result.data == {"parsed": '{"answer": "ok"}'}


@pytest.mark.asyncio
async def test_generate_provider_error_records_failure_and_reauthentication(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    entity = _entity()
    entity.hass = hass
    error = OpenAIError("provider failed")
    calls: list[tuple[str, object]] = []

    async def fail_handle(*args: Any, **kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(entity, "_async_handle_chat_log", fail_handle)
    monkeypatch.setattr(
        ai_task_platform,
        "request_reauthentication",
        lambda _hass, entry, err: calls.append(("reauth", err)),
    )
    monkeypatch.setattr(
        ai_task_platform,
        "record_current_provider_failure",
        lambda err: calls.append(("record", err)),
    )
    monkeypatch.setattr(
        ai_task_platform,
        "log_provider_failure",
        lambda *args: calls.append(("log", args[-1])),
    )
    chat_log = SimpleNamespace(
        llm_api=None,
        content=[],
        conversation_id="conversation-3",
    )

    with pytest.raises(OpenAIError, match="provider failed"):
        await entity._async_generate_data(
            cast(Any, SimpleNamespace(name="plain", structure=None)),
            cast(Any, chat_log),
        )

    assert calls == [("reauth", error), ("record", error), ("log", error)]


@pytest.mark.asyncio
async def test_generate_rejects_non_assistant_final_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity = _entity()

    async def fake_handle(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(entity, "_async_handle_chat_log", fake_handle)
    chat_log = SimpleNamespace(
        llm_api=None,
        content=[object()],
        conversation_id="conversation-4",
    )

    with pytest.raises(
        HomeAssistantError,
        match="Last content in chat log is not an AssistantContent",
    ):
        await entity._async_generate_data(
            cast(Any, SimpleNamespace(name="plain", structure=None)),
            cast(Any, chat_log),
        )
