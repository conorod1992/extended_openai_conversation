"""Focused residual coverage for the agent configuration self-test."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import OpenAIError

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


def _entry(client: Any, **data: Any) -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry-1", runtime_data=client, data=data)


def _subentry(**data: Any) -> SimpleNamespace:
    return SimpleNamespace(subentry_id="agent-1", data=data)


def _patch_common(monkeypatch: pytest.MonkeyPatch, usage: _Usage | None = None) -> _Usage:
    usage = usage or _Usage()
    monkeypatch.setattr(agent_test, "_validate_function_schema", lambda _subentry: 2)
    monkeypatch.setattr(agent_test, "get_loaded_guest_mode", lambda *args: None)
    monkeypatch.setattr(
        agent_test,
        "resolve_guest_policy",
        lambda *args: SimpleNamespace(
            guest_active=False,
            as_diagnostics=lambda: {
                "readable_entity_count": 0,
                "configured_tool_count": 0,
            },
        ),
    )
    monkeypatch.setattr(agent_test, "configured_function_tools_from_data", lambda _data: [])
    monkeypatch.setattr(agent_test, "get_exposed_entities", lambda _hass: [{"entity_id": "light.kitchen"}])
    monkeypatch.setattr(agent_test, "memory_enabled", lambda _data: False)
    monkeypatch.setattr(agent_test, "supports_openai_hosted_tools", lambda *_args: False)

    async def get_usage(*_args: Any) -> _Usage:
        return usage

    monkeypatch.setattr(agent_test, "async_get_usage", get_usage)
    return usage


def _checks(result: agent_test.AgentTestResult) -> dict[str, agent_test.TestCheck]:
    return {check.name: check for check in result.checks}


def test_result_helpers_cover_warning_failure_and_rendering() -> None:
    checks = [
        agent_test.TestCheck("One", "Passed", "ok"),
        agent_test.TestCheck("Two", "Warning", "careful"),
    ]
    result = agent_test.AgentTestResult(agent_test._overall(checks), checks)

    assert result.status == "Warning"
    assert result.as_dict() == {
        "status": "Warning",
        "checks": [
            {"name": "One", "status": "Passed", "message": "ok"},
            {"name": "Two", "status": "Warning", "message": "careful"},
        ],
        "authentication_rejected": False,
    }
    assert "Overall: Warning" in result.as_text()
    assert "Two: Warning — careful" in result.as_text()
    assert agent_test._overall([agent_test._check("x", "Failed", "bad")]) == "Failed"
    assert agent_test._overall([agent_test._check("x", "Passed", "ok")]) == "Passed"


@pytest.mark.asyncio
async def test_agent_test_fails_fast_without_client(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(None)),
        cast(Any, _subentry()),
    )

    assert result.status == "Failed"
    assert _checks(result)["Authentication"].message == "API client is unavailable"


@pytest.mark.asyncio
async def test_agent_test_rejects_unknown_api_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(SimpleNamespace())),
        cast(Any, _subentry(api_mode="future_mode")),
    )

    assert result.status == "Failed"
    assert _checks(result)["API mode"].message == "Unsupported mode: future_mode"


@pytest.mark.asyncio
async def test_agent_test_reports_configuration_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_test,
        "_validate_function_schema",
        lambda _subentry: (_ for _ in ()).throw(ValueError("bad tool schema")),
    )

    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(SimpleNamespace())),
        cast(Any, _subentry()),
    )

    assert result.status == "Failed"
    assert _checks(result)["Configuration"].message == "bad tool schema"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("loader", "installed", "expected_status", "expected_text"),
    [
        (SimpleNamespace(available=False, reason="loader disabled"), None, "Failed", "loader disabled"),
        (
            SimpleNamespace(available=True, on_demand=False, group_id=None),
            ["other"],
            "Failed",
            "Selected but not installed: weather",
        ),
        (
            SimpleNamespace(available=True, on_demand=True, group_id="skills"),
            ["weather"],
            "Passed",
            "1 enabled and loadable through on-demand group `skills`",
        ),
    ],
)
async def test_agent_test_skill_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    loader: Any,
    installed: list[str] | None,
    expected_status: str,
    expected_text: str,
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(agent_test, "validate_function_groups", lambda *_args: {})
    monkeypatch.setattr(agent_test, "skill_loader_status", lambda *_args, **_kwargs: loader)

    if installed is not None:
        manager = SimpleNamespace(
            get_all_skills=lambda: [SimpleNamespace(name=name) for name in installed]
        )

        async def get_instance(_hass: Any) -> Any:
            return manager

        monkeypatch.setattr(agent_test.SkillManager, "async_get_instance", get_instance)

    create = _Create(result=SimpleNamespace(usage=None))
    client = SimpleNamespace(
        responses=SimpleNamespace(create=create.create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create.create)),
    )
    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(client)),
        cast(Any, _subentry(skills=["weather"])),
    )

    check = _checks(result)["Skills"]
    assert check.status == expected_status
    assert check.message == expected_text


@pytest.mark.asyncio
async def test_agent_test_reports_skill_and_memory_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        agent_test,
        "validate_function_groups",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("groups broken")),
    )
    monkeypatch.setattr(agent_test, "memory_enabled", lambda _data: True)

    async def fail_memory(*_args: Any) -> Any:
        raise RuntimeError("memory broken")

    monkeypatch.setattr(agent_test, "async_get_memory", fail_memory)
    create = _Create(result=SimpleNamespace(usage=None))
    client = SimpleNamespace(responses=SimpleNamespace(create=create.create))

    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(client)),
        cast(Any, _subentry(skills=["weather"])),
    )

    checks = _checks(result)
    assert checks["Skills"].message == "groups broken"
    assert checks["Persistent memory"].status == "Failed"
    assert checks["Persistent memory"].message == "RuntimeError"


@pytest.mark.asyncio
async def test_agent_test_flags_incompatible_web_search_without_probe_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    create = _Create(result=SimpleNamespace(usage=None))
    client = SimpleNamespace(responses=SimpleNamespace(create=create.create))

    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(client, api_provider="other")),
        cast(Any, _subentry(web_search=True)),
    )

    check = _checks(result)["Web Search"]
    assert check.status == "Failed"
    assert "does not support" in check.message


@pytest.mark.asyncio
async def test_agent_test_authentication_failure_requests_reauth_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = _patch_common(monkeypatch)
    error = OpenAIError("bad auth")
    create = _Create(error=error)
    client = SimpleNamespace(responses=SimpleNamespace(create=create.create))
    calls: list[str] = []

    monkeypatch.setattr(agent_test, "classify_config_provider_error", lambda _err: "invalid_auth")
    monkeypatch.setattr(agent_test, "provider_user_message", lambda _err: "Authentication failed")
    monkeypatch.setattr(agent_test, "request_reauthentication", lambda *_args: calls.append("reauth"))

    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(client)),
        cast(Any, _subentry()),
    )

    checks = _checks(result)
    assert calls == ["reauth"]
    assert result.authentication_rejected is True
    assert checks["Authentication"].status == "Failed"
    assert checks["Authentication"].message == "Authentication failed"
    assert checks["Model access"].message == "Authentication rejected"
    assert usage.calls == [{"successful": False}]


@pytest.mark.asyncio
async def test_agent_test_non_auth_provider_error_and_web_search_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = _patch_common(monkeypatch)
    monkeypatch.setattr(agent_test, "supports_openai_hosted_tools", lambda *_args: True)
    monkeypatch.setattr(agent_test, "classify_config_provider_error", lambda _err: "rate_limit")
    monkeypatch.setattr(agent_test, "provider_user_message", lambda _err: "Rate limited")
    error = OpenAIError("slow down")
    create = _Create(error=error)
    client = SimpleNamespace(responses=SimpleNamespace(create=create.create))

    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(client)),
        cast(Any, _subentry(web_search=True)),
    )

    checks = _checks(result)
    assert result.authentication_rejected is False
    assert checks["Model access"].message == "Rate limited"
    assert checks["Function calling"].message == "Probe was rejected"
    assert checks["Web Search"].message == "Rate limited"
    assert usage.calls == [{"successful": False}]


@pytest.mark.asyncio
async def test_agent_test_unexpected_probe_error_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = _patch_common(monkeypatch)
    create = _Create(error=RuntimeError("transport exploded"))
    client = SimpleNamespace(responses=SimpleNamespace(create=create.create))

    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(client)),
        cast(Any, _subentry()),
    )

    checks = _checks(result)
    assert checks["Model access"].message == "transport exploded"
    assert checks["Function calling"].status == "Failed"
    assert usage.calls == [{"successful": False}]


@pytest.mark.asyncio
async def test_agent_test_successful_responses_probe_records_usage_and_web_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = _patch_common(monkeypatch)
    monkeypatch.setattr(agent_test, "supports_openai_hosted_tools", lambda *_args: True)
    monkeypatch.setattr(agent_test, "ensure_successful_responses_result", lambda response: None)
    monkeypatch.setattr(agent_test, "extract_usage", lambda raw: {"input_tokens": 3} if raw == "usage" else {})
    create = _Create(result=SimpleNamespace(usage="usage"))
    client = SimpleNamespace(responses=SimpleNamespace(create=create.create))

    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(client)),
        cast(Any, _subentry(web_search=True)),
    )

    checks = _checks(result)
    assert checks["Model access"].status == "Passed"
    assert checks["Function calling"].status == "Passed"
    assert checks["Web Search"].status == "Passed"
    assert create.calls[0]["tools"][0]["type"] == "web_search"
    assert create.calls[0]["tool_choice"] == "none"
    assert usage.calls == [{"successful": True, "usage": {"input_tokens": 3}}]


@pytest.mark.asyncio
async def test_agent_test_chat_completions_probe_uses_wrapped_function_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = _patch_common(monkeypatch)
    monkeypatch.setattr(agent_test, "get_api_mode", lambda *_args: agent_test.API_MODE_CHAT_COMPLETIONS)
    monkeypatch.setattr(agent_test, "extract_usage", lambda _raw: {})
    create = _Create(result=SimpleNamespace(usage=None))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create.create)))

    result = await agent_test.async_test_agent(
        cast(Any, SimpleNamespace()),
        cast(Any, _entry(client)),
        cast(Any, _subentry(api_mode=agent_test.API_MODE_CHAT_COMPLETIONS)),
    )

    assert result.status == "Passed"
    kwargs = create.calls[0]
    assert kwargs["stream"] is False
    assert kwargs["tools"][0]["type"] == "function"
    assert kwargs["tools"][0]["function"]["name"] == "configuration_test_noop"
    assert "type" not in kwargs["tools"][0]["function"]
