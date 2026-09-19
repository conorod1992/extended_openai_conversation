"""Focused residual coverage for runtime failure containment."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from openai import OpenAIError

from custom_components.extended_openai_conversation_responses import (
    runtime_failure_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)


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
    _async_dispatch_function_tool = (
        ExtendedOpenAIAgentEntity._async_dispatch_function_tool
    )

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


async def test_archive_wrapper_delegates_non_archive_and_blocks_disallowed_guest(
    monkeypatch,
) -> None:
    original = AsyncMock(return_value="delegated")
    original._extended_openai_archive_failure_label = False
    installed = ExtendedOpenAIAgentEntity._execute_function_tool
    assert ExtendedOpenAIAgentEntity._execute_function_tool is installed

    entity = _ArchiveEntity(guest_active=True, allowed=False)
    tool_input = SimpleNamespace(tool_args={"query": "hello"})

    blocked = await installed(
        entity,
        {"function": {"type": "archive", "operation": "search"}},
        tool_input,
        None,
        [],
    )

    assert blocked == {
        "status": "error",
        "error": "This capability is unavailable in Guest Mode.",
    }
    assert entity.archive_calls == []


async def test_archive_wrapper_maps_value_error_without_mislabeling_as_unavailable(
    monkeypatch,
) -> None:
    original = AsyncMock(return_value="unused")
    original._extended_openai_archive_failure_label = False

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
