"""Focused residual coverage for runtime failure containment."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from openai import OpenAIError

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    runtime_failure_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses import usage as usage_module
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)


class _Hass:
    def __init__(self) -> None:
        self.data: dict = {}


class _Usage:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def mark_current_run_failed(self, error_type: str) -> None:
        self.failed.append(error_type)


class _ConversationEntity:
    def __init__(self) -> None:
        self.hass = SimpleNamespace()
        self.entry = SimpleNamespace()
        self._usage = _Usage()
        self.finished: list[tuple[str, str | None]] = []

    def _fire_conversation_finished(
        self, _user_input, _chat_log, *, status: str, error_type: str | None = None
    ) -> None:
        self.finished.append((status, error_type))


class _ArchiveEntity:
    def __init__(self, *, guest_active: bool = False, allowed: bool = True) -> None:
        self.guest_active = guest_active
        self.allowed = allowed
        self.archive_calls: list[tuple[str, dict]] = []

    def _effective_guest_policy(self):
        return SimpleNamespace(guest_active=self.guest_active)

    def _guest_integration_allowed(self, integration: str, operation: str) -> bool:
        assert integration == "archive"
        return self.allowed

    async def _async_execute_archive_tool(self, operation: str, arguments: dict):
        self.archive_calls.append((operation, arguments))
        return {"status": "ok"}

    def _tool_result(self, _tool_input, result):
        return result


async def test_usage_fallback_discards_poisoned_persistent_manager(monkeypatch) -> None:
    hass = _Hass()
    key = ("entry", "agent")
    other_key = ("entry", "other")
    poisoned = object()
    other = object()
    hass.data[usage_module._USAGE_MANAGERS] = {key: poisoned, other_key: other}

    async def fail_getter(_hass, _entry_id: str, _subentry_id: str):
        raise OSError("corrupt usage store")

    monkeypatch.setattr(hardening, "_ORIGINAL_ASYNC_GET_USAGE", fail_getter)

    fallback = await hardening.async_get_usage_safely(hass, *key)

    assert key not in hass.data[usage_module._USAGE_MANAGERS]
    assert hass.data[usage_module._USAGE_MANAGERS][other_key] is other
    assert hass.data[hardening._VOLATILE_USAGE_MANAGERS][key] is fallback
    assert fallback._initialized is True


async def test_usage_fallback_does_not_require_persistent_manager_registry(monkeypatch) -> None:
    hass = _Hass()

    async def fail_getter(_hass, _entry_id: str, _subentry_id: str):
        raise OSError("store unavailable")

    monkeypatch.setattr(hardening, "_ORIGINAL_ASYNC_GET_USAGE", fail_getter)

    manager = await hardening.async_get_usage_safely(hass, "entry", "agent")

    assert manager is hass.data[hardening._VOLATILE_USAGE_MANAGERS][("entry", "agent")]


def test_usage_startup_installer_replaces_live_import_aliases_once(monkeypatch) -> None:
    async def current_getter(_hass, _entry_id: str, _subentry_id: str):
        return None

    alias_module = ModuleType(
        "custom_components.extended_openai_conversation_responses._coverage_usage_alias"
    )
    alias_module.async_get_usage = current_getter
    unrelated = ModuleType(
        "custom_components.extended_openai_conversation_responses._coverage_unrelated"
    )
    unrelated.async_get_usage = object()
    monkeypatch.setitem(sys.modules, alias_module.__name__, alias_module)
    monkeypatch.setitem(sys.modules, unrelated.__name__, unrelated)
    monkeypatch.setattr(usage_module, "async_get_usage", current_getter)

    hardening._install_usage_startup_fallback()
    first = usage_module.async_get_usage
    hardening._install_usage_startup_fallback()

    assert first is hardening.async_get_usage_safely
    assert usage_module.async_get_usage is first
    assert alias_module.async_get_usage is first
    assert unrelated.async_get_usage is not first


def test_openai_conversation_error_uses_provider_failure_path(monkeypatch) -> None:
    entity = _ConversationEntity()
    user_input = SimpleNamespace(language="en", conversation_id="conversation-1")
    error = OpenAIError("provider unavailable")
    reauth = Mock()
    record = Mock()
    log = Mock()
    monkeypatch.setattr(hardening, "request_reauthentication", reauth)
    monkeypatch.setattr(hardening, "record_current_provider_failure", record)
    monkeypatch.setattr(hardening, "log_provider_failure", log)
    monkeypatch.setattr(hardening, "provider_user_message", lambda _err: "try again")

    result = hardening._conversation_error_result(
        entity, user_input, SimpleNamespace(), error
    )

    assert result.conversation_id == "conversation-1"
    assert entity._usage.failed == ["OpenAIError"]
    assert entity.finished == [("error", "OpenAIError")]
    reauth.assert_called_once_with(entity.hass, entity.entry, error)
    record.assert_called_once_with(error)
    log.assert_called_once()


async def test_request_preparation_boundary_catches_supported_errors_and_is_idempotent(
    monkeypatch,
) -> None:
    calls = 0

    async def failing_handle(_entity, _user_input, _chat_log, _request_options=None):
        nonlocal calls
        calls += 1
        raise HomeAssistantError("bad preparation")

    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_handle_message", failing_handle)
    monkeypatch.setattr(
        hardening,
        "_conversation_error_result",
        lambda _entity, _input, _log, err: ("contained", type(err).__name__),
    )

    hardening._install_request_preparation_boundary()
    installed = ExtendedOpenAIAgentEntity._async_handle_message
    hardening._install_request_preparation_boundary()

    result = await ExtendedOpenAIAgentEntity._async_handle_message(
        object(), object(), object(), {"temperature": 0}
    )

    assert result == ("contained", "HomeAssistantError")
    assert calls == 1
    assert ExtendedOpenAIAgentEntity._async_handle_message is installed


async def test_archive_wrapper_delegates_non_archive_and_blocks_disallowed_guest(
    monkeypatch,
) -> None:
    original = AsyncMock(return_value="delegated")
    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_execute_function_tool", original)
    hardening._install_archive_failure_label()
    installed = ExtendedOpenAIAgentEntity._execute_function_tool
    hardening._install_archive_failure_label()
    assert ExtendedOpenAIAgentEntity._execute_function_tool is installed

    entity = _ArchiveEntity(guest_active=True, allowed=False)
    tool_input = SimpleNamespace(tool_args={"query": "hello"})

    delegated = await installed(
        entity, {"function": {"type": "native"}}, tool_input, None, []
    )
    blocked = await installed(
        entity,
        {"function": {"type": "archive", "operation": "search"}},
        tool_input,
        None,
        [],
    )

    assert delegated == "delegated"
    assert blocked == {"status": "error", "error": hardening.GUEST_MODE_UNAVAILABLE}
    assert entity.archive_calls == []
    original.assert_awaited_once()


async def test_archive_wrapper_maps_value_error_without_mislabeling_as_unavailable(
    monkeypatch,
) -> None:
    original = AsyncMock(return_value="unused")
    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_execute_function_tool", original)
    hardening._install_archive_failure_label()

    entity = _ArchiveEntity()

    async def invalid_archive(_operation: str, _arguments: dict):
        raise ValueError("bad archive arguments")

    entity._async_execute_archive_tool = invalid_archive
    result = await ExtendedOpenAIAgentEntity._execute_function_tool(
        entity,
        {"function": {"type": "archive", "operation": "search"}},
        SimpleNamespace(tool_args={}),
        None,
        [],
    )

    assert result == {"status": "error", "error": "bad archive arguments"}


async def _chunks(*tool_deltas):
    for tool_calls in tool_deltas:
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(tool_calls=tool_calls))]
        )


async def test_late_tool_id_repair_falls_back_for_non_dataclass_tool_inputs(monkeypatch) -> None:
    async def original(_entity, _chat_log, result, _request_usage=None):
        async for _chunk in result:
            pass
        yield {
            "tool_calls": [
                SimpleNamespace(tool_name="search", tool_args={"q": "x"}, external=False)
            ]
        }

    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_transform_chat_stream", original)
    hardening._install_late_chat_tool_call_id_repair()

    output = [
        item
        async for item in ExtendedOpenAIBaseLLMEntity._transform_chat_stream(
            object(),
            object(),
            _chunks(
                [SimpleNamespace(index="bad", id="ignored")],
                [SimpleNamespace(index=0, id=None)],
                [SimpleNamespace(index=0, id="call-late")],
            ),
        )
    ]

    repaired = output[0]["tool_calls"][0]
    assert repaired.id == "call-late"
    assert repaired.tool_name == "search"
    assert repaired.tool_args == {"q": "x"}
    assert repaired.external is False


async def test_late_tool_id_repair_leaves_unrepairable_calls_unchanged(monkeypatch) -> None:
    existing = SimpleNamespace(id="already-present", tool_name="one", tool_args={})
    missing = SimpleNamespace(id=None, tool_name="two", tool_args={})
    payload = {"tool_calls": [existing, missing]}

    async def original(_entity, _chat_log, result, _request_usage=None):
        async for _chunk in result:
            pass
        yield payload
        yield {"content": "done"}

    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_transform_chat_stream", original)
    hardening._install_late_chat_tool_call_id_repair()
    installed = ExtendedOpenAIBaseLLMEntity._transform_chat_stream
    hardening._install_late_chat_tool_call_id_repair()
    assert ExtendedOpenAIBaseLLMEntity._transform_chat_stream is installed

    output = [
        item
        async for item in installed(
            object(),
            object(),
            _chunks([SimpleNamespace(index=0, id="late-but-not-needed")]),
        )
    ]

    assert output[0] is payload
    assert output[0]["tool_calls"] == [existing, missing]
    assert output[1] == {"content": "done"}


def test_top_level_install_is_idempotent(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(hardening, "_INSTALLED", False)
    monkeypatch.setattr(
        hardening, "_install_usage_startup_fallback", lambda: calls.append("usage")
    )
    monkeypatch.setattr(
        hardening, "_install_request_preparation_boundary", lambda: calls.append("request")
    )
    monkeypatch.setattr(
        hardening, "_install_archive_failure_label", lambda: calls.append("archive")
    )
    monkeypatch.setattr(
        hardening,
        "_install_late_chat_tool_call_id_repair",
        lambda: calls.append("tool-id"),
    )

    hardening.install_runtime_failure_hardening()
    hardening.install_runtime_failure_hardening()

    assert calls == ["usage", "request", "archive", "tool-id"]
