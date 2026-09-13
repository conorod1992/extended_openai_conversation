"""Focused conversation security-boundary coverage."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_SHARED_MEMORY_MODE,
    SHARED_MEMORY_DISABLED,
    SHARED_MEMORY_EXPLICIT,
)


Agent = conversation_module.ExtendedOpenAIAgentEntity


def _policy(**overrides: Any) -> SimpleNamespace:
    values = {
        "guest_active": False,
        "legacy_function_flags": False,
        "shared_memory_write": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("latest_tools", "case"),
    [
        ([], "removed"),
        (
            [
                {
                    "spec": {"name": "control_light"},
                    "function": {"type": "script"},
                    "guest_allowed": False,
                }
            ],
            "guest_access_revoked",
        ),
    ],
)
async def test_guest_configured_tool_is_revalidated_against_latest_config(
    monkeypatch: pytest.MonkeyPatch,
    latest_tools: list[dict[str, Any]],
    case: str,
) -> None:
    """A tool exposed earlier must fail closed if its live config is tightened."""
    exposed_tool = {
        "spec": {"name": "control_light"},
        "function": {"type": "script"},
        "guest_allowed": True,
    }
    latest_data = {"revision": case}
    latest_subentry = SimpleNamespace(data=latest_data)
    latest_entry = SimpleNamespace(
        subentries={"subentry-id": latest_subentry},
    )
    seen_data: list[Any] = []

    def configured_from_data(data: Any) -> list[dict[str, Any]]:
        seen_data.append(data)
        return latest_tools

    agent = SimpleNamespace(
        hass=SimpleNamespace(
            config_entries=SimpleNamespace(
                async_get_entry=lambda _entry_id: latest_entry,
            )
        ),
        entry=SimpleNamespace(entry_id="entry-id"),
        subentry=SimpleNamespace(subentry_id="subentry-id", data={"revision": "old"}),
        _effective_guest_policy=lambda: _policy(
            guest_active=True,
            legacy_function_flags=True,
        ),
        _configured_function_tools_from_data=configured_from_data,
        _tool_result=lambda _tool_input, result: result,
    )

    monkeypatch.setattr(
        conversation_module,
        "latest_function_tool_for_execution",
        lambda _agent, tool: tool,
    )

    result = await Agent._execute_function_tool(
        agent,
        exposed_tool,
        SimpleNamespace(tool_args={}),
        None,
        [],
    )

    assert seen_data == [latest_data]
    assert result["status"] == "denied"
    assert result["reason"] == "guest_mode"


def _memory_agent(
    *,
    shared_mode: str = SHARED_MEMORY_DISABLED,
    policy: SimpleNamespace | None = None,
) -> SimpleNamespace:
    effective_policy = policy or _policy()
    return SimpleNamespace(
        subentry=SimpleNamespace(
            data={CONF_SHARED_MEMORY_MODE: shared_mode},
        ),
        _effective_guest_policy=lambda: effective_policy,
    )


def test_shared_conversation_cannot_write_personal_memory() -> None:
    """Shared conversations must never infer or select a personal owner."""
    agent = _memory_agent(shared_mode=SHARED_MEMORY_EXPLICIT)
    token = conversation_module._ACTIVE_SCOPE.set(
        SimpleNamespace(scope_type="shared", user_id=None, scope_id="household")
    )
    try:
        with pytest.raises(
            ValueError,
            match="shared household conversations cannot write personal memory",
        ):
            Agent._current_write_memory_scope_id(
                agent,
                "personal",
                None,
                source="explicit",
            )
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)


def test_personal_conversation_cannot_write_disabled_household_memory() -> None:
    """An explicit household selector must respect disabled shared memory."""
    agent = _memory_agent(shared_mode=SHARED_MEMORY_DISABLED)
    token = conversation_module._ACTIVE_SCOPE.set(
        SimpleNamespace(scope_type="user", user_id="user-1", scope_id="user-1")
    )
    try:
        with pytest.raises(ValueError, match="shared household memory is disabled"):
            Agent._current_write_memory_scope_id(
                agent,
                "household",
                None,
                source="explicit",
            )
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)


def test_implicit_household_write_requires_automatic_shared_memory() -> None:
    """Explicit-only shared memory must reject automatic/implicit writes."""
    agent = _memory_agent(shared_mode=SHARED_MEMORY_EXPLICIT)
    token = conversation_module._ACTIVE_SCOPE.set(
        SimpleNamespace(scope_type="user", user_id="user-1", scope_id="user-1")
    )
    try:
        with pytest.raises(
            ValueError,
            match="automatic shared memory creation is disabled",
        ):
            Agent._current_write_memory_scope_id(
                agent,
                "household",
                None,
                source="implicit",
            )
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)


def test_guest_cannot_select_personal_memory_for_write() -> None:
    """Guest write permission is household-only even when writes are enabled."""
    agent = _memory_agent(
        shared_mode=SHARED_MEMORY_EXPLICIT,
        policy=_policy(guest_active=True, shared_memory_write=True),
    )
    token = conversation_module._ACTIVE_SCOPE.set(
        SimpleNamespace(scope_type="user", user_id="user-1", scope_id="user-1")
    )
    try:
        with pytest.raises(RuntimeError) as err:
            Agent._current_write_memory_scope_id(
                agent,
                "personal",
                None,
                source="explicit",
            )
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)

    assert str(err.value) == conversation_module.GUEST_MODE_UNAVAILABLE
