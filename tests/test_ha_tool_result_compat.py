"""Home Assistant ToolResultContent compatibility tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from custom_components.extended_openai_conversation_responses import ha_tool_result_compat


def test_make_tool_result_content_uses_legacy_api(monkeypatch: Any) -> None:
    """Use tool_result= on Home Assistant versions with the legacy constructor."""
    captured: dict[str, Any] = {}
    sentinel = object()

    def legacy_tool_result_content(
        *, agent_id: str, tool_call_id: str, tool_name: str, tool_result: dict[str, Any]
    ) -> object:
        captured.update(
            agent_id=agent_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_result=tool_result,
        )
        return sentinel

    monkeypatch.setattr(
        ha_tool_result_compat.conversation,
        "ToolResultContent",
        legacy_tool_result_content,
    )
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

    captured: dict[str, Any] = {}
    sentinel = object()

    def new_tool_result_content(
        *, agent_id: str, tool_call_id: str, tool_name: str, result: FakeToolResult
    ) -> object:
        captured.update(
            agent_id=agent_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result,
        )
        return sentinel

    monkeypatch.setattr(
        ha_tool_result_compat.conversation,
        "ToolResultContent",
        new_tool_result_content,
    )
    monkeypatch.setattr(
        ha_tool_result_compat.llm, "ToolResult", FakeToolResult, raising=False
    )
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


def test_production_tool_result_construction_stays_centralized() -> None:
    """Prevent direct version-sensitive ToolResultContent construction elsewhere."""
    root = Path(__file__).parents[1] / "custom_components" / "extended_openai_conversation_responses"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "ha_tool_result_compat.py":
            continue
        if "ToolResultContent(" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
