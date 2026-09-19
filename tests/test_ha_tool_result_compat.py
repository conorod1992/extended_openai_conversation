"""Home Assistant ToolResultContent compatibility tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from custom_components.extended_openai_conversation_responses import (
    ha_tool_result_compat,
)


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
    root = (
        Path(__file__).parents[1]
        / "custom_components"
        / "extended_openai_conversation_responses"
    )
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "ha_tool_result_compat.py":
            continue
        if "ToolResultContent(" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_tool_result_data_round_trips_the_installed_ha_api() -> None:
    """The real HA content type preserves the exact data object, old or new."""
    payload = {"result": {"nested": [1, {"message": "café"}]}}
    content = ha_tool_result_compat.make_tool_result_content(
        agent_id="conversation.test",
        tool_call_id="roundtrip",
        tool_name="lookup",
        tool_result=payload,
    )
    assert ha_tool_result_compat.tool_result_data(content) is payload


def test_tool_result_data_legacy_and_absent_values() -> None:
    """Older result content, optional results and raw service values still work."""
    from types import SimpleNamespace

    payload = {"result": "old"}
    assert (
        ha_tool_result_compat.tool_result_data(SimpleNamespace(tool_result=payload))
        is payload
    )
    for value in (None, {}, [], "", 0, False):
        assert (
            ha_tool_result_compat.tool_result_data(SimpleNamespace(tool_result=value))
            is value
        )
        assert (
            ha_tool_result_compat.tool_result_data(
                SimpleNamespace(result=value, tool_result="wrong")
            )
            is value
        )
    raw_service_result = {"answer": 42}
    assert ha_tool_result_compat.tool_result_data(raw_service_result) is None
    assert (
        ha_tool_result_compat.tool_result_data(
            raw_service_result, default=raw_service_result
        )
        is raw_service_result
    )


def test_tool_result_data_never_reads_new_api_deprecated_property(
    monkeypatch: Any,
) -> None:
    """Do not evaluate the legacy property even as an eager getattr default."""
    from custom_components.extended_openai_conversation_responses import (
        model_tool_results,
        request_diagnostics,
    )

    @dataclass
    class ModernResult:
        data: dict[str, Any]

    class ModernContent:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.result = ModernResult(payload)

        @property
        def tool_result(self) -> Any:
            raise AssertionError("deprecated property must never be accessed")

    monkeypatch.setattr(
        ha_tool_result_compat.llm, "ToolResult", ModernResult, raising=False
    )
    payload = {"result": '{ "a": 1, "b": [2, 3] }'}
    content = ModernContent(payload)
    assert ha_tool_result_compat.tool_result_data(content) is payload
    assert request_diagnostics._result_characters(content) == len(payload["result"])
    assert model_tool_results._compact_json_result_content(content) is content
    assert payload["result"] == '{"a":1,"b":[2,3]}'
    assert content.result.data is payload


def test_tool_result_data_does_not_hide_result_access_failures() -> None:
    """Compatibility is not a blanket error/warning suppression layer."""
    import pytest

    class BrokenContent:
        @property
        def result(self) -> Any:
            raise RuntimeError("unexpected result failure")

    with pytest.raises(RuntimeError, match="unexpected result failure"):
        ha_tool_result_compat.tool_result_data(BrokenContent())


def test_production_tool_result_reads_stay_centralized() -> None:
    """All production legacy reads/probes must stay behind the version boundary."""
    import ast

    root = (
        Path(__file__).parents[1]
        / "custom_components"
        / "extended_openai_conversation_responses"
    )
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "ha_tool_result_compat.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            direct = isinstance(node, ast.Attribute) and node.attr == "tool_result"
            probe = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "hasattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "tool_result"
            )
            if direct or probe:
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == []
