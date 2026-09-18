"""Home Assistant ToolResultContent compatibility tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from custom_components.extended_openai_conversation_responses import ha_tool_result_compat


def _capture_constructor(monkeypatch: Any) -> tuple[dict[str, Any], object]:
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_tool_result_content(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        ha_tool_result_compat.conversation, "ToolResultContent", fake_tool_result_content
    )
    return captured, sentinel


def test_make_tool_result_content_uses_legacy_api(monkeypatch: Any) -> None:
    """Use tool_result= on Home Assistant versions without llm.ToolResult."""
    captured, sentinel = _capture_constructor(monkeypatch)
    monkeypatch.delattr(ha_tool_result_compat.llm, "ToolResult", raising=False)
    payload = {"result": "ok"}

    result = ha_tool_result_compat.make_tool_result_content(
        agent_id="conversation.test",
        tool_call_id="call-1",
        tool_name="get_state",
        tool_result=payload,
    )

    assert result is sentinel
    assert captured == {
        "agent_id": "conversation.test",
        "tool_call_id": "call-1",
        "tool_name": "get_state",
        "tool_result": payload,
    }


def test_make_tool_result_content_uses_new_api(monkeypatch: Any) -> None:
    """Use result=llm.ToolResult on newer Home Assistant versions."""

    @dataclass(slots=True)
    class FakeToolResult:
        data: dict[str, Any]
        error: bool = False

    captured, sentinel = _capture_constructor(monkeypatch)
    monkeypatch.setattr(ha_tool_result_compat.llm, "ToolResult", FakeToolResult, raising=False)
    payload = {"result": "ok"}

    result = ha_tool_result_compat.make_tool_result_content(
        agent_id="conversation.test",
        tool_call_id="call-2",
        tool_name="get_state",
        tool_result=payload,
    )

    assert result is sentinel
    assert captured["agent_id"] == "conversation.test"
    assert captured["tool_call_id"] == "call-2"
    assert captured["tool_name"] == "get_state"
    assert "tool_result" not in captured
    assert isinstance(captured["result"], FakeToolResult)
    assert captured["result"].data == payload
    assert captured["result"].error is False
