"""Completion event snapshots keep retained backend payload traversal off HA's loop."""

import asyncio
from contextlib import closing
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components import conversation

from custom_components.extended_openai_conversation_responses import (
    conversation as owner,
)
from custom_components.extended_openai_conversation_responses.functions.sqlite import (
    _execute_sqlite_query,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    make_tool_result_content,
)


@pytest.mark.parametrize("rows", [10, 10000])
async def test_completion_copies_real_sqlite_history_in_workers(
    tmp_path, monkeypatch, rows
):
    contents = await retained_sqlite_history(tmp_path, rows)
    expected = [item.as_dict() for item in contents]
    agent = object.__new__(owner.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(subentry_id="agent")
    agent.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))
    loop_thread = threading.get_ident()
    calls = []
    original = conversation.ToolResultContent.as_dict

    def as_dict(content):
        calls.append(threading.get_ident())
        return original(content)

    monkeypatch.setattr(conversation.ToolResultContent, "as_dict", as_dict)
    user_input = SimpleNamespace()

    async def process(request):
        agent._fire_conversation_finished(
            request, SimpleNamespace(content=contents), status="success"
        )
        # HA can replace the chat log list after completion. Membership is captured.
        contents.clear()
        return "response"

    agent._async_process = process
    assert await agent.async_process(user_input) == "response"
    assert calls and all(thread != loop_thread for thread in calls)
    agent.hass.bus.async_fire.assert_called_once()
    payload = agent.hass.bus.async_fire.call_args.args[1]
    assert payload == {
        "user_input": user_input,
        "messages": expected,
        "agent_id": "agent",
        "status": "success",
    }
    # The event has been fired by the time normal/direct processing returns.
    assert len(payload["messages"]) == 7


async def retained_sqlite_history(tmp_path, rows):
    path = tmp_path / "report.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE report (a BLOB,b BLOB,c BLOB,d BLOB,e BLOB,f BLOB,g BLOB,h BLOB)"
        )
        connection.executemany(
            "INSERT INTO report VALUES (?,?,?,?,?,?,?,?)", [(b"x",) * 8] * rows
        )
    contents = []
    for index in range(5):
        result = await asyncio.to_thread(
            _execute_sqlite_query,
            str(path),
            "SELECT * FROM report",
            False,
            10000,
            10,
            4 * 1024 * 1024,
        )
        contents.append(
            make_tool_result_content(
                agent_id="agent",
                tool_call_id=f"call-{index}",
                tool_name="report",
                tool_result={"result": result},
            )
        )
    contents.extend(
        [
            conversation.UserContent(content="Thanks"),
            conversation.AssistantContent(agent_id="agent", content="You're welcome"),
        ]
    )
    return contents


@pytest.mark.parametrize("direct", [False, True])
async def test_queued_completion_survives_request_cancellation(direct):
    agent = object.__new__(owner.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(subentry_id="agent")
    agent.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

    async def cancel(request):
        agent._fire_conversation_finished(
            request,
            SimpleNamespace(content=[]),
            status="error",
            error_type="CancelledError",
        )
        raise asyncio.CancelledError

    agent._async_process = cancel
    with pytest.raises(asyncio.CancelledError):
        await (
            agent.async_process_direct(object())
            if direct
            else agent.async_process(object())
        )
    agent.hass.bus.async_fire.assert_called_once()
    assert agent.hass.bus.async_fire.call_args.args[1]["error_type"] == "CancelledError"
