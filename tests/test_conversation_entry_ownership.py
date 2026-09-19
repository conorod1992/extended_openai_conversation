"""Contracts for explicit conversation request-entry ownership."""

from __future__ import annotations

import ast
import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_maintenance,
    conversation as owner,
    debug,
    ha_permissions,
    request_static_cache,
    scope,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_TIMEOUT_MINUTES,
)
from homeassistant.core import Context

_ENTRY_METHODS = (
    "async_process",
    "async_process_direct",
    "_async_process",
    "_async_process_with_continuity",
    "_async_process_claimed",
)
_PROTECTED = frozenset(
    (
        *_ENTRY_METHODS,
        "conversation_request_lease",
        "conversation_debug_trace",
        "formatted_tool_cache",
        "voice_identity_scope",
    )
)


def _replacement_lines(source: str) -> list[int]:
    violations: list[int] = []
    for node in ast.walk(ast.parse(source)):
        targets = []
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = (
                node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            )
        for target in targets:
            for part in ast.walk(target):
                if (
                    isinstance(part, ast.Attribute)
                    and part.attr in _PROTECTED
                    or isinstance(part, ast.Name)
                    and part.id in _PROTECTED
                    or isinstance(part, ast.Subscript)
                    and isinstance(part.slice, ast.Constant)
                    and part.slice.value in _PROTECTED
                ):
                    violations.append(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"setattr", "delattr"} and len(node.args) >= 2:
                name = node.args[1]
                if isinstance(name, ast.Constant) and name.value in _PROTECTED:
                    violations.append(node.lineno)
    return violations


def test_entry_methods_and_scope_owners_cannot_be_replaced() -> None:
    root = Path(owner.__file__).parent
    violations = [
        f"{path.name}:{line}"
        for path in root.rglob("*.py")
        for line in _replacement_lines(path.read_text())
    ]
    assert violations == []
    for name in _ENTRY_METHODS:
        method = getattr(owner.ExtendedOpenAIAgentEntity, name)
        assert method.__module__ == owner.__name__
        assert not hasattr(method, "__wrapped__")


@pytest.mark.parametrize(
    "code",
    [
        "Agent._async_process = replacement",
        "setattr(Agent, '_async_process', replacement)",
        "delattr(Agent, '_async_process')",
        "del Agent._async_process",
        "_async_process_with_continuity = replacement",
        "owner.voice_identity_scope = wrapper",
    ],
)
def test_structural_guard_detects_replacement_forms(code: str) -> None:
    assert _replacement_lines(code)


@pytest.mark.parametrize("direct", [False, True])
async def test_explicit_order_is_shared_by_assist_and_direct(
    entry_agent, entry_input, monkeypatch, direct
) -> None:
    events: list[str] = []
    trace = SimpleNamespace(result=None)

    @contextmanager
    def bind(context):
        assert context is entry_input.context
        events.append("caller-enter")
        try:
            yield
        finally:
            events.append("caller-exit")

    @asynccontextmanager
    async def lease(agent):
        assert agent is entry_agent
        events.append("maintenance-enter")
        try:
            yield
        finally:
            events.append("maintenance-exit")

    @contextmanager
    def capture(agent, request):
        assert agent is entry_agent and request is entry_input
        events.append("debug-enter")
        try:
            yield trace
        finally:
            events.append("debug-exit")

    @contextmanager
    def cache():
        events.append("cache-enter")
        try:
            yield
        finally:
            events.append("cache-exit")

    @asynccontextmanager
    async def voice(agent, request):
        assert agent is entry_agent and request is entry_input
        events.append("voice-enter")
        try:
            yield
        finally:
            events.append("voice-exit")

    async def reconcile(agent):
        assert agent is entry_agent
        events.append("reconcile")

    async def process(request):
        assert request is entry_input
        events.append("process")
        return "done"

    monkeypatch.setattr(owner, "bind_active_ha_context", bind)
    monkeypatch.setattr(owner, "conversation_request_lease", lease)
    monkeypatch.setattr(owner, "conversation_debug_trace", capture)
    monkeypatch.setattr(owner, "formatted_tool_cache", cache)
    monkeypatch.setattr(owner, "voice_identity_scope", voice)
    monkeypatch.setattr(owner, "async_reconcile_runtime_configuration", reconcile)
    entry_agent._async_process_with_continuity = process

    if direct:
        result, metadata = await entry_agent.async_process_direct(entry_input)
        assert result == "done"
        assert metadata == {"handled_locally": False}
    else:
        assert await entry_agent.async_process(entry_input) == "done"

    assert events == [
        "caller-enter",
        "maintenance-enter",
        "debug-enter",
        "cache-enter",
        "voice-enter",
        "reconcile",
        "process",
        "voice-exit",
        "cache-exit",
        "debug-exit",
        "maintenance-exit",
        "caller-exit",
    ]
    assert trace.result == "done"


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_entry_cleanup_restores_outer_context(
    entry_agent, entry_input, failure
) -> None:
    entry_agent._async_process_with_continuity.side_effect = failure("stopped")
    prior_context = Context(user_id="outer")
    outer_cache = {"outer": "cache"}
    outer_voice = frozenset({"outer"})
    tokens = [
        (ha_permissions._ACTIVE_HA_CONTEXT, ha_permissions._ACTIVE_HA_CONTEXT.set(prior_context)),
        (request_static_cache._FORMATTED_TOOLS, request_static_cache._FORMATTED_TOOLS.set(outer_cache)),
        (scope._ACTIVE_VOICE_IDENTITY_USERS, scope._ACTIVE_VOICE_IDENTITY_USERS.set(outer_voice)),
    ]
    try:
        with pytest.raises(failure, match="stopped"):
            await entry_agent.async_process(entry_input)
        assert ha_permissions.get_active_ha_context() is prior_context
        assert request_static_cache._FORMATTED_TOOLS.get() is outer_cache
        assert scope._ACTIVE_VOICE_IDENTITY_USERS.get() is outer_voice
        assert entry_input.satellite_id == "assist_satellite.kitchen"
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


@pytest.mark.parametrize("stage", ["llm_context", "scope", "resolve", "claimed", "release"])
async def test_continuity_stage_restores_guest_and_prompt_state(
    monkeypatch, stage
) -> None:
    policy = SimpleNamespace(guest_active=False)
    resolution = SimpleNamespace(key="key", claim_token="claim")
    continuity = SimpleNamespace(
        async_resolve=AsyncMock(return_value=resolution),
        async_release=AsyncMock(),
    )
    agent = SimpleNamespace(
        subentry=SimpleNamespace(data={}),
        _continuity=continuity,
        _resolve_live_guest_policy=lambda: policy,
        _async_process_claimed=AsyncMock(return_value="result"),
    )
    request = SimpleNamespace(
        as_llm_context=Mock(return_value=SimpleNamespace(context=Context())),
        satellite_id=None,
        device_id="device",
        conversation_id=None,
    )
    resolver = Mock(return_value=SimpleNamespace(device_id="device"))
    monkeypatch.setattr(owner, "resolve_data_scope", resolver)
    target = {
        "llm_context": request.as_llm_context,
        "scope": resolver,
        "resolve": continuity.async_resolve,
        "claimed": agent._async_process_claimed,
        "release": continuity.async_release,
    }[stage]
    target.side_effect = RuntimeError("stopped")
    old_guest, old_cache = object(), object()
    guest_token = owner._ACTIVE_GUEST_POLICY.set(old_guest)
    cache_token = owner._PROMPT_CACHE_CONTEXT.set(old_cache)
    try:
        with pytest.raises(RuntimeError, match="stopped"):
            await owner.ExtendedOpenAIAgentEntity._async_process_with_continuity(
                agent, request
            )
        assert owner._ACTIVE_GUEST_POLICY.get() is old_guest
        assert owner._PROMPT_CACHE_CONTEXT.get() is old_cache
        if stage in {"claimed", "release"}:
            continuity.async_release.assert_awaited_once_with("key", "claim")
        else:
            continuity.async_release.assert_not_awaited()
    finally:
        owner._ACTIVE_GUEST_POLICY.reset(guest_token)
        owner._PROMPT_CACHE_CONTEXT.reset(cache_token)


async def test_invalid_timeout_before_claim_resets_guest_policy(monkeypatch) -> None:
    agent = SimpleNamespace(
        subentry=SimpleNamespace(
            data={CONF_CONVERSATION_TIMEOUT_MINUTES: "invalid"}
        ),
        _continuity=SimpleNamespace(async_resolve=AsyncMock()),
        _resolve_live_guest_policy=lambda: SimpleNamespace(guest_active=False),
    )
    request = SimpleNamespace(
        as_llm_context=lambda _: SimpleNamespace(context=Context()),
        satellite_id=None,
        device_id="device",
        conversation_id=None,
    )
    monkeypatch.setattr(
        owner, "resolve_data_scope", lambda *_: SimpleNamespace(device_id="device")
    )
    prior = object()
    token = owner._ACTIVE_GUEST_POLICY.set(prior)
    try:
        with pytest.raises(ValueError):
            await owner.ExtendedOpenAIAgentEntity._async_process_with_continuity(
                agent, request
            )
        assert owner._ACTIVE_GUEST_POLICY.get() is prior
        agent._continuity.async_resolve.assert_not_awaited()
    finally:
        owner._ACTIVE_GUEST_POLICY.reset(token)
