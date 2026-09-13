"""Residual coverage for the agent configuration self-test."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import agent_test


class _Usage:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def async_record_request(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _Create:
    def __init__(self, *, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def _subentry_with_tools(tools: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(data={agent_test.CONF_FUNCTION_TOOLS: yaml.safe_dump(tools)})


def test_validate_function_schema_rejects_missing_function_mapping() -> None:
    subentry = SimpleNamespace(
        data={agent_test.CONF_FUNCTION_TOOLS: yaml.safe_dump([{"type": "function"}])}
    )

    with pytest.raises(ValueError, match="function mapping"):
        agent_test._validate_function_schema(cast(Any, subentry))


def test_validate_function_schema_dispatches_ha_tool_to_reference_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function_config = {"name": "ha_tool", "type": "native"}
    subentry = _subentry_with_tools([{"type": "function", "function": function_config}])
    validate = MagicMock()

    monkeypatch.setattr(agent_test, "is_ha_tool", lambda _tool: True)
    monkeypatch.setattr(agent_test, "validate_reference", validate)
    monkeypatch.setattr(
        agent_test,
        "get_function",
        lambda _kind: pytest.fail("HA tools must not use custom function validation"),
    )

    assert agent_test._validate_function_schema(cast(Any, subentry)) == 1
    validate.assert_called_once_with(function_config)


def test_validate_function_schema_dispatches_custom_tool_by_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function_config = {"name": "custom", "type": "template"}
    subentry = _subentry_with_tools([{"type": "function", "function": function_config}])
    validate = MagicMock()
    get_function = MagicMock(return_value=SimpleNamespace(validate_schema=validate))

    monkeypatch.setattr(agent_test, "is_ha_tool", lambda _tool: False)
    monkeypatch.setattr(agent_test, "get_function", get_function)

    assert agent_test._validate_function_schema(cast(Any, subentry)) == 1
    get_function.assert_called_once_with("template")
    validate.assert_called_once_with(function_config)


async def _run_agent(
    monkeypatch: pytest.MonkeyPatch,
    *,
    web_search: bool = False,
    probe_error: BaseException | None = None,
    memory_is_enabled: bool = False,
) -> tuple[agent_test.AgentTestResult, _Usage]:
    usage = _Usage()
    create = _Create(result=SimpleNamespace(usage=None), error=probe_error)
    client = SimpleNamespace(responses=SimpleNamespace(create=create.create))
    entry = SimpleNamespace(entry_id="entry-1", runtime_data=client, data={})
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        data={
            agent_test.CONF_API_MODE: agent_test.API_MODE_RESPONSES,
            agent_test.CONF_CHAT_MODEL: "gpt-5.6",
            agent_test.CONF_FUNCTION_TOOLS: "[]",
            agent_test.CONF_WEB_SEARCH: web_search,
        },
    )

    monkeypatch.setattr(agent_test, "_validate_function_schema", lambda _subentry: 0)
    monkeypatch.setattr(agent_test, "get_api_mode", lambda *_args: agent_test.API_MODE_RESPONSES)
    monkeypatch.setattr(
        agent_test,
        "get_loaded_guest_mode",
        lambda *_args: SimpleNamespace(status=lambda: {"state": "active"}),
    )
    monkeypatch.setattr(agent_test, "configured_function_tools_from_data", lambda _data: [])
    monkeypatch.setattr(
        agent_test,
        "resolve_guest_policy",
        lambda *_args: SimpleNamespace(
            guest_active=True,
            as_diagnostics=lambda: {
                "readable_entity_count": 3,
                "configured_tool_count": 2,
            },
        ),
    )
    monkeypatch.setattr(
        agent_test,
        "get_exposed_entities",
        lambda _hass: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )
    monkeypatch.setattr(agent_test, "memory_enabled", lambda _data: memory_is_enabled)
    monkeypatch.setattr(agent_test, "supports_openai_hosted_tools", lambda *_args: True)
    monkeypatch.setattr(agent_test, "ensure_successful_responses_result", lambda _response: None)
    monkeypatch.setattr(agent_test, "extract_usage", lambda _usage: {})

    if memory_is_enabled:
        async def get_memory(*_args: Any) -> Any:
            return SimpleNamespace(stats=lambda: {"memory_count": 4})

        monkeypatch.setattr(agent_test, "async_get_memory", get_memory)

    async def get_usage(*_args: Any) -> _Usage:
        return usage

    monkeypatch.setattr(agent_test, "async_get_usage", get_usage)

    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()), cast(Any, entry), cast(Any, subentry)
    )
    return result, usage


@pytest.mark.asyncio
async def test_guest_active_message_and_exposed_entity_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _usage = await _run_agent(monkeypatch)
    checks = {check.name: check for check in result.checks}

    assert checks["Guest Mode"].status == "Passed"
    assert checks["Guest Mode"].message == "Active; 3 visible entities; 2 custom tools"
    assert checks["Exposed entities"].status == "Warning"
    assert checks["Exposed entities"].message == "0"


@pytest.mark.asyncio
async def test_successful_memory_check_reports_stored_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _usage = await _run_agent(monkeypatch, memory_is_enabled=True)
    memory = next(check for check in result.checks if check.name == "Persistent memory")

    assert memory.status == "Passed"
    assert memory.message == "Available (4 stored)"


@pytest.mark.asyncio
async def test_unexpected_probe_error_marks_compatible_web_search_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, usage = await _run_agent(
        monkeypatch,
        web_search=True,
        probe_error=RuntimeError("probe transport failed"),
    )
    checks = {check.name: check for check in result.checks}

    assert checks["Model access"].status == "Failed"
    assert checks["Web Search"].status == "Failed"
    assert checks["Web Search"].message == "probe transport failed"
    assert usage.calls == [{"successful": False}]
