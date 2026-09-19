"""Conversation request ownership, ordering and task-local cleanup contracts."""
from __future__ import annotations

import ast
import asyncio
from contextlib import asynccontextmanager, contextmanager
from copy import copy
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import Context

from custom_components.extended_openai_conversation_responses import (
    agent_maintenance, conversation, debug, ha_permissions, request_static_cache,
    scope as scope_module, voice_identity_runtime,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_VOICE_DEFAULT_USER_ID, CONF_VOICE_DEVICE_MAPPINGS, CONF_VOICE_SCOPE_POLICY,
    VOICE_POLICY_DEFAULT_USER, VOICE_POLICY_DEVICE_MAPPING,
)

_ENTRY_METHODS = frozenset({
    'async_process', 'async_process_direct', '_async_process',
    '_async_process_with_continuity', '_async_process_claimed',
})
_HELPERS = frozenset({
    'conversation_request_lease', 'conversation_debug_trace',
    'formatted_tool_cache', 'voice_identity_scope',
})
_ROOT = Path(__file__).resolve().parents[1]


def _replacement_lines(source):
    """Detect assignment/deletion or attribute-map replacement of entry owners."""
    protected = _ENTRY_METHODS | _HELPERS
    hits = []
    def target_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            return node.slice.value
        return None
    for node in ast.walk(ast.parse(source)):
        targets = []
        if isinstance(node, (ast.Assign, ast.Delete)):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        elif isinstance(node, ast.Call):
            called = target_name(node.func)
            if called in {'setattr', 'delattr'} and len(node.args) >= 2:
                name = node.args[1]
                if isinstance(name, ast.Constant) and name.value in protected:
                    hits.append(node.lineno)
        if any(target_name(target) in protected for target in targets):
            hits.append(node.lineno)
    return sorted(set(hits))


def test_production_cannot_replace_request_entry_or_its_owners():
    violations = []
    root = _ROOT / 'custom_components/extended_openai_conversation_responses'
    for path in root.rglob('*.py'):
        violations.extend(f'{path.relative_to(root)}:{line}' for line in _replacement_lines(path.read_text()))
    assert not violations, '\n'.join(violations)
    for name in _ENTRY_METHODS:
        method = getattr(conversation.ExtendedOpenAIAgentEntity, name)
        assert method.__module__ == conversation.__name__
        assert not hasattr(method, '__wrapped__')


@pytest.mark.parametrize('source', [
    'Agent._async_process = wrapper',
    'setattr(Agent, "_async_process", wrapper)',
    'delattr(Agent, "async_process_direct")',
    'del Agent._async_process_claimed',
    'Agent.__dict__["_async_process"] = wrapper',
    'conversation.formatted_tool_cache = wrapper',
    '_async_process: object = wrapper',
])
def test_structural_detector_rejects_replacement_forms(source):
    assert _replacement_lines(source)


def test_repeated_actual_setup_never_changes_entry_identity():
    code = '''
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import conversation

async def main():
    cls = conversation.ExtendedOpenAIAgentEntity
    names = ('async_process', 'async_process_direct', '_async_process', '_async_process_with_continuity', '_async_process_claimed')
    original = {name: getattr(cls, name) for name in names}
    hass = MagicMock()
    hass.data = {}
    hass.auth.async_get_users = AsyncMock(return_value=[])
    async_names = ('async_setup_model_catalog', 'async_get_quiet_hours', 'async_setup_delayed_tools', 'async_migrate_integration', 'async_recover_pending_restores', 'async_setup_services', 'async_setup_intercom_services', 'async_setup_debug_ui', 'async_setup_management_ui')
    from contextlib import ExitStack
    with ExitStack() as stack:
        for name in async_names:
            stack.enter_context(patch.object(integration, name, AsyncMock()))
        for name in ('setup_provider_credentials_websocket', 'setup_backup_transfer_websocket'):
            stack.enter_context(patch.object(integration, name, MagicMock()))
        for _ in range(2):
            assert await integration.async_setup(hass, {})
            for name, method in original.items():
                assert getattr(cls, name) is method, name
                assert not hasattr(method, '__wrapped__'), name
    assert hass.auth.async_get_users.await_count == 2
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=_ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('direct', [False, True])
async def test_public_paths_use_the_same_explicit_stage_order(entry_agent, entry_input, monkeypatch, direct):
    events = []
    holder = SimpleNamespace(result=None)
    @contextmanager
    def caller(context):
        assert context is entry_input.context
        events.append('caller-enter')
        try:
            yield
        finally:
            events.append('caller-exit')
    @asynccontextmanager
    async def lease(agent):
        assert agent is entry_agent
        events.append('maintenance-enter')
        try:
            yield
        finally:
            events.append('maintenance-exit')
    @contextmanager
    def capture(agent, request):
        assert agent is entry_agent and request is entry_input
        events.append('debug-enter')
        try:
            yield holder
        finally:
            assert holder.result == 'done'
            events.append('debug-finish')
    @contextmanager
    def cache():
        events.append('cache-enter')
        try:
            yield
        finally:
            events.append('cache-exit')
    @asynccontextmanager
    async def voice(agent, request):
        events.append('voice-enter')
        try:
            yield
        finally:
            events.append('voice-exit')
    async def reconcile(agent):
        assert agent is entry_agent
        events.append('reconcile')
    async def core(request):
        assert request is entry_input
        events.append('continuity-and-processing')
        if direct:
            conversation._PROCESS_METADATA.get()['handled_locally'] = True
        return 'done'
    for name, helper in [('bind_active_ha_context', caller), ('conversation_request_lease', lease), ('conversation_debug_trace', capture), ('formatted_tool_cache', cache), ('voice_identity_scope', voice), ('async_reconcile_runtime_configuration', reconcile)]:
        monkeypatch.setattr(conversation, name, helper)
    entry_agent._async_process_with_continuity = core
    result = await (entry_agent.async_process_direct(entry_input) if direct else entry_agent.async_process(entry_input))
    assert result == (('done', {'handled_locally': True}) if direct else 'done')
    assert events == ['caller-enter', 'maintenance-enter', 'debug-enter', 'cache-enter', 'voice-enter', 'reconcile', 'continuity-and-processing', 'voice-exit', 'cache-exit', 'debug-finish', 'maintenance-exit', 'caller-exit']


@pytest.mark.parametrize('stage', ['identity', 'reconcile', 'processing'])
@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
async def test_failures_restore_every_outer_context_and_release_gate(entry_agent, entry_input, monkeypatch, stage, failure):
    entry_agent.subentry.data = {CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEFAULT_USER, CONF_VOICE_DEFAULT_USER_ID: 'voice-owner'}
    entry_agent.hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_active=True))
    manager = debug.get_debug_manager(entry_agent.hass, 'entry', 'agent')
    manager.configure(enabled=True)
    error = failure('stopped')
    if stage == 'identity':
        entry_agent.hass.auth.async_get_user.side_effect = error
    elif stage == 'reconcile':
        monkeypatch.setattr(conversation, 'async_reconcile_runtime_configuration', AsyncMock(side_effect=error))
    else:
        entry_agent._async_process_with_continuity.side_effect = error
    variables = [ha_permissions._ACTIVE_HA_CONTEXT, request_static_cache._FORMATTED_TOOLS, scope_module._ACTIVE_VOICE_IDENTITY_USERS, debug._ACTIVE_DEBUG_TRACE, conversation._PROCESS_METADATA]
    priors = [Context(user_id='outer'), {'outer': True}, frozenset({'outer'}), object(), {'outer': True}]
    tokens = [var.set(value) for var, value in zip(variables, priors, strict=True)]
    try:
        with pytest.raises(failure):
            await entry_agent.async_process_direct(entry_input)
        for var, prior in zip(variables, priors, strict=True):
            assert var.get() is prior
        assert entry_input.satellite_id == 'assist_satellite.kitchen'
        assert manager.status()['count'] == 1
        assert manager.summaries()[0]['successful'] is False
        if stage != 'processing':
            entry_agent._async_process_with_continuity.assert_not_awaited()
        gate = agent_maintenance.get_agent_maintenance_gate(entry_agent.hass, 'entry', 'agent')
        async with asyncio.timeout(1):
            async with gate.exclusive():
                pass
    finally:
        for var, token in reversed(list(zip(variables, tokens, strict=True))):
            var.reset(token)


async def test_debug_finishes_inside_gate_after_cache_and_voice_restore(entry_agent, entry_input, monkeypatch):
    manager = debug.get_debug_manager(entry_agent.hass, 'entry', 'agent')
    manager.configure(enabled=True)
    finish = manager.finish
    gate = agent_maintenance.get_agent_maintenance_gate(entry_agent.hass, 'entry', 'agent')
    outer_cache = {'outer': 'preserved'}
    outer_users = frozenset({'outer'})
    cache_token = request_static_cache._FORMATTED_TOOLS.set(outer_cache)
    user_token = scope_module._ACTIVE_VOICE_IDENTITY_USERS.set(outer_users)
    observed = []
    def check(trace, **kwargs):
        assert gate._active_readers == 1
        assert ha_permissions.get_active_ha_context() is entry_input.context
        assert request_static_cache._FORMATTED_TOOLS.get() is outer_cache
        assert scope_module._ACTIVE_VOICE_IDENTITY_USERS.get() is outer_users
        assert entry_input.satellite_id == 'assist_satellite.kitchen'
        observed.append(kwargs['result'])
        return finish(trace, **kwargs)
    monkeypatch.setattr(manager, 'finish', check)
    try:
        assert await entry_agent.async_process(entry_input) == 'processed'
    finally:
        request_static_cache._FORMATTED_TOOLS.reset(cache_token)
        scope_module._ACTIVE_VOICE_IDENTITY_USERS.reset(user_token)
    assert observed == ['processed']
    assert gate._active_readers == 0


async def test_maintenance_wait_precedes_debug_identity_and_live_reconciliation(entry_agent, entry_input, monkeypatch):
    manager = debug.get_debug_manager(entry_agent.hass, 'entry', 'agent')
    manager.configure(enabled=True)
    begin = Mock(wraps=manager.begin)
    monkeypatch.setattr(manager, 'begin', begin)
    seen = []
    async def reconcile(agent):
        seen.append(agent.subentry.data['revision'])
    monkeypatch.setattr(conversation, 'async_reconcile_runtime_configuration', reconcile)
    gate = agent_maintenance.get_agent_maintenance_gate(entry_agent.hass, 'entry', 'agent')
    async with gate.exclusive():
        task = asyncio.create_task(entry_agent.async_process(entry_input))
        await asyncio.sleep(0)
        assert not task.done()
        assert seen == []
        begin.assert_not_called()
        entry_agent.hass.auth.async_get_user.assert_not_awaited()
        entry_agent.subentry.data = {'revision': 'after-restore'}
    assert await task == 'processed'
    assert seen == ['after-restore']
    assert begin.call_count == 1


async def test_cancelled_maintenance_wait_does_not_prepare_request(entry_agent, entry_input, monkeypatch):
    reconcile = AsyncMock()
    monkeypatch.setattr(conversation, 'async_reconcile_runtime_configuration', reconcile)
    prior = Context(user_id='prior')
    async def invoke():
        with ha_permissions.bind_active_ha_context(prior):
            try:
                await entry_agent.async_process_direct(entry_input)
            finally:
                assert ha_permissions.get_active_ha_context() is prior
    gate = agent_maintenance.get_agent_maintenance_gate(entry_agent.hass, 'entry', 'agent')
    async with gate.exclusive():
        task = asyncio.create_task(invoke())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    reconcile.assert_not_awaited()
    entry_agent._async_process_with_continuity.assert_not_awaited()
    assert entry_input.satellite_id == 'assist_satellite.kitchen'
    assert gate._active_readers == 0


async def test_concurrent_public_paths_keep_callers_voice_owners_caches_and_capture_isolated(entry_agent, entry_input):
    left, right = copy(entry_input), copy(entry_input)
    left.context, right.context = Context(), Context()
    left.device_id, right.device_id = 'left', 'right'
    entry_agent.subentry.data = {CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING, CONF_VOICE_DEVICE_MAPPINGS: {'left': 'user:alice', 'right': 'user:bob'}}
    entry_agent.hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_active=True))
    manager = debug.get_debug_manager(entry_agent.hass, 'entry', 'agent')
    manager.configure(enabled=True)
    arrivals = []
    ready = asyncio.Event()
    async def core(request):
        caller = ha_permissions.get_active_ha_context()
        users = scope_module._ACTIVE_VOICE_IDENTITY_USERS.get()
        cache = request_static_cache._FORMATTED_TOOLS.get()
        trace = debug.current_debug_trace()
        metadata = conversation._PROCESS_METADATA.get()
        cache['device'] = request.device_id
        arrivals.append((cache, trace))
        if len(arrivals) == 2:
            ready.set()
        await ready.wait()
        assert caller is request.context
        assert ha_permissions.get_active_ha_context() is caller
        assert users == frozenset({'alice' if request.device_id == 'left' else 'bob'})
        assert scope_module._ACTIVE_VOICE_IDENTITY_USERS.get() is users
        assert request_static_cache._FORMATTED_TOOLS.get() is cache
        assert cache['device'] == request.device_id
        assert debug.current_debug_trace() is trace
        assert conversation._PROCESS_METADATA.get() is metadata
        assert (metadata is not None) == (request.device_id == 'right')
        if metadata is not None:
            metadata['handled_locally'] = True
        return request.device_id
    entry_agent._async_process_with_continuity = core
    token = conversation._PROCESS_METADATA.set(None)
    try:
        async with asyncio.timeout(2):
            results = await asyncio.gather(entry_agent.async_process(left), entry_agent.async_process_direct(right))
    finally:
        conversation._PROCESS_METADATA.reset(token)
    assert results == ['left', ('right', {'handled_locally': True})]
    assert arrivals[0][0] is not arrivals[1][0]
    assert arrivals[0][1] is not arrivals[1][1]
    assert manager.status()['count'] == 2
    assert left.satellite_id == right.satellite_id == 'assist_satellite.kitchen'


@pytest.mark.parametrize('stage', ['llm_context', 'scope', 'resolve', 'claimed', 'release'])
@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
async def test_continuity_stage_restores_policy_and_prompt_context(stage, failure, entry_input, monkeypatch):
    policy = SimpleNamespace(guest_active=False)
    resolution = SimpleNamespace(key='key', claim_token='claim')
    continuity = SimpleNamespace(async_resolve=AsyncMock(return_value=resolution), async_release=AsyncMock())
    agent = SimpleNamespace(subentry=SimpleNamespace(data={}), _continuity=continuity, _resolve_live_guest_policy=lambda: policy, _async_process_claimed=AsyncMock(return_value='done'))
    selected_scope = SimpleNamespace(device_id=None)
    monkeypatch.setattr(conversation, 'resolve_data_scope', lambda *_: selected_scope)
    error = failure('stopped')
    if stage == 'llm_context':
        entry_input.as_llm_context = Mock(side_effect=error)
    elif stage == 'scope':
        monkeypatch.setattr(conversation, 'resolve_data_scope', Mock(side_effect=error))
    elif stage == 'resolve':
        continuity.async_resolve.side_effect = error
    elif stage == 'claimed':
        agent._async_process_claimed.side_effect = error
    else:
        continuity.async_release.side_effect = error
    old_guest, old_prompt = object(), object()
    guest_token = conversation._ACTIVE_GUEST_POLICY.set(old_guest)
    prompt_token = conversation._PROMPT_CACHE_CONTEXT.set(old_prompt)
    try:
        with pytest.raises(failure):
            await conversation.ExtendedOpenAIAgentEntity._async_process_with_continuity(agent, entry_input)
        assert conversation._ACTIVE_GUEST_POLICY.get() is old_guest
        assert conversation._PROMPT_CACHE_CONTEXT.get() is old_prompt
    finally:
        conversation._ACTIVE_GUEST_POLICY.reset(guest_token)
        conversation._PROMPT_CACHE_CONTEXT.reset(prompt_token)
    if stage in {'claimed', 'release'}:
        continuity.async_release.assert_awaited_once_with('key', 'claim')
    else:
        continuity.async_release.assert_not_awaited()


async def test_invalid_timeout_before_claim_does_not_leak_guest_policy(entry_input, monkeypatch):
    continuity = SimpleNamespace(async_resolve=AsyncMock())
    agent = SimpleNamespace(subentry=SimpleNamespace(data={CONF_CONVERSATION_TIMEOUT_MINUTES: 'invalid'}), _continuity=continuity, _resolve_live_guest_policy=lambda: SimpleNamespace(guest_active=False))
    monkeypatch.setattr(conversation, 'resolve_data_scope', lambda *_: SimpleNamespace(device_id=None))
    prior = object()
    token = conversation._ACTIVE_GUEST_POLICY.set(prior)
    try:
        with pytest.raises(ValueError):
            await conversation.ExtendedOpenAIAgentEntity._async_process_with_continuity(agent, entry_input)
        assert conversation._ACTIVE_GUEST_POLICY.get() is prior
    finally:
        conversation._ACTIVE_GUEST_POLICY.reset(token)
    continuity.async_resolve.assert_not_awaited()
