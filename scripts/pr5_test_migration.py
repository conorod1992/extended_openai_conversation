"""Temporary reviewed test migration; production transports are removed before review."""
import ast
from pathlib import Path


def replace_function(text, name, replacement):
    nodes = [n for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(nodes) == 1, (name, len(nodes))
    n = nodes[0]
    lines = text.splitlines(keepends=True)
    start = min([n.lineno, *[d.lineno for d in n.decorator_list]]) - 1
    lines[start:n.end_lineno] = [replacement.strip() + '\n']
    return ''.join(lines)


def transform_function(text, name, transform):
    n, = [n for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    lines = text.splitlines(keepends=True)
    start = min([n.lineno, *[d.lineno for d in n.decorator_list]]) - 1
    old = ''.join(lines[start:n.end_lineno])
    new = transform(old)
    assert new != old, name
    lines[start:n.end_lineno] = [new]
    return ''.join(lines)


def write(path, text):
    ast.parse(text)
    Path(path).write_text(text.rstrip() + '\n')


def remove_patch_call(text, attribute):
    lines = text.splitlines(keepends=True)
    spans = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if isinstance(call.func, ast.Attribute) and call.func.attr == 'setattr' and len(call.args) > 1 and isinstance(call.args[1], ast.Constant) and call.args[1].value == attribute:
            spans.append((node.lineno - 1, node.end_lineno))
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    return ''.join(lines)


if 'def entry_agent(' not in Path('tests/conftest.py').read_text():
    path = 'tests/conftest.py'
    write(path, Path(path).read_text() + '''

@pytest.fixture
def entry_agent(hass, monkeypatch):
    """Real request owner with only the already-claimed processing stage stubbed."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from custom_components.extended_openai_conversation_responses import conversation
    agent = object.__new__(conversation.ExtendedOpenAIAgentEntity)
    agent.hass = hass
    agent.entry = SimpleNamespace(entry_id="entry", data={})
    agent.subentry = SimpleNamespace(subentry_id="agent", data={})
    agent._async_process_with_continuity = AsyncMock(return_value="processed")
    hass.auth.async_get_user = AsyncMock(return_value=None)
    monkeypatch.setattr(conversation, "async_reconcile_runtime_configuration", AsyncMock())
    return agent


@pytest.fixture
def entry_input():
    """Request-shaped input with both device-registry and satellite metadata."""
    from types import SimpleNamespace
    from homeassistant.core import Context
    request = SimpleNamespace(
        text="hello", language="en", context=Context(), conversation_id=None,
        device_id="device-registry-id", satellite_id="assist_satellite.kitchen",
    )
    request.as_llm_context = lambda _domain: SimpleNamespace(context=request.context)
    return request
''')

    path = 'tests/test_request_static_cache_coverage.py'
    s = Path(path).read_text().replace('        "process": agent_type._async_process,\n', '').replace('        agent_type._async_process = originals["process"]\n', '')
    s = replace_function(s, 'test_process_wrapper_uses_fresh_request_cache_and_restores_outer_context', '''
async def test_owned_process_uses_fresh_cache_and_restores_outer_context(entry_agent, entry_input):
    seen = []
    async def core(request):
        seen.append(request_static_cache._FORMATTED_TOOLS.get())
        request_static_cache._FORMATTED_TOOLS.get()["inside"] = request.text
        return "ok"
    entry_agent._async_process_with_continuity = core
    outer = {"outer": "preserved"}
    token = request_static_cache._FORMATTED_TOOLS.set(outer)
    try:
        result = await entry_agent.async_process(entry_input)
        assert request_static_cache._FORMATTED_TOOLS.get() is outer
    finally:
        request_static_cache._FORMATTED_TOOLS.reset(token)
    assert result == "ok"
    assert seen == [{"inside": "hello"}]
    assert outer == {"outer": "preserved"}
''')
    s = replace_function(s, 'test_process_wrapper_restores_cache_when_processing_raises', '''
async def test_owned_process_restores_cache_when_processing_raises(entry_agent, entry_input):
    async def core(request):
        assert request_static_cache._FORMATTED_TOOLS.get() == {}
        raise RuntimeError("boom")
    entry_agent._async_process_with_continuity = core
    outer = {"outer": "still-here"}
    token = request_static_cache._FORMATTED_TOOLS.set(outer)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await entry_agent.async_process(entry_input)
        assert request_static_cache._FORMATTED_TOOLS.get() is outer
    finally:
        request_static_cache._FORMATTED_TOOLS.reset(token)
''')
    write(path, s)

    path = 'tests/test_voice_identity_runtime.py'
    s = Path(path).read_text().replace('from custom_components.extended_openai_conversation_responses import conversation\n', '')
    s = replace_function(s, 'test_installed_wrapper_uses_registry_device_and_restores_satellite', '''
async def test_owned_entry_uses_registry_device_and_restores_satellite(entry_agent, entry_input):
    observed = []
    async def core(request):
        observed.append((request.device_id, request.satellite_id))
        return "processed"
    entry_agent._async_process_with_continuity = core
    assert await entry_agent.async_process(entry_input) == "processed"
    assert observed == [("device-registry-id", None)]
    assert entry_input.satellite_id == "assist_satellite.kitchen"
    entry_agent.hass.auth.async_get_user.assert_not_awaited()
''')
    s = replace_function(s, 'test_runtime_stale_mapping_follows_unmapped_policy', '''
async def test_runtime_stale_mapping_follows_unmapped_policy(entry_agent, entry_input):
    options = {
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        CONF_VOICE_DEVICE_MAPPINGS: {"device-registry-id": "user:deleted-user"},
        CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICY_SHARED,
    }
    entry_agent.subentry.data = options
    observed = []
    async def core(request):
        observed.append(resolve_data_scope(SimpleNamespace(
            context=request.context,
            device_id=request.satellite_id or request.device_id,
        ), options))
        return "processed"
    entry_agent._async_process_with_continuity = core
    entry_agent.hass.auth.async_get_user.return_value = None
    await entry_agent.async_process(entry_input)
    assert observed[0].scope_id == SHARED_HOUSEHOLD_SCOPE_ID
    assert observed[0].source == "shared_voice_policy"
    entry_agent.hass.auth.async_get_user.assert_awaited_once_with("deleted-user")
    assert entry_input.satellite_id == "assist_satellite.kitchen"
''')
    s = replace_function(s, 'test_installer_recognizes_existing_voice_identity_wrapper', '''
async def test_voice_identity_scope_restores_satellite_when_validation_is_cancelled(entry_agent, entry_input, monkeypatch):
    import asyncio
    monkeypatch.setattr(voice_identity_runtime, "_active_configured_users", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        async with voice_identity_runtime.voice_identity_scope(entry_agent, entry_input):
            pytest.fail("cancelled validation must not admit the request")
    assert entry_input.satellite_id == "assist_satellite.kitchen"
''')
    write(path, s)

    path = 'tests/test_ha_permissions.py'
    s = Path(path).read_text()
    s = s.replace('from custom_components.extended_openai_conversation_responses.conversation import (\n    ExtendedOpenAIAgentEntity,\n)\n', '')
    s = replace_function(s, 'test_request_context_binding_wraps_once_and_restores_context', '''
async def test_owned_entry_binds_actual_caller_and_restores_context(entry_agent, entry_input):
    caller = Context(user_id="user-1")
    prior = Context(user_id="prior-user")
    entry_input.context = caller
    seen = []
    async def core(request):
        seen.append(ha_permissions.get_active_ha_context())
        return "result"
    entry_agent._async_process_with_continuity = core
    with ha_permissions.bind_active_ha_context(prior):
        assert await entry_agent.async_process(entry_input) == "result"
        assert ha_permissions.get_active_ha_context() is prior
    assert seen == [caller]
''')
    s = remove_patch_call(s, '_install_request_context_binding')
    write(path, s)

    path = 'tests/test_agent_maintenance_coverage.py'
    s = Path(path).read_text()
    s = replace_function(s, 'test_conversation_guard_bypasses_partial_entities_and_gates_real_identity', '''
async def test_conversation_lease_bypasses_partial_entities_and_gates_real_identity(monkeypatch):
    gate = RecordingGate()
    monkeypatch.setattr(agent_maintenance, "get_agent_maintenance_gate", lambda *_: gate)
    async with agent_maintenance.conversation_request_lease(SimpleNamespace()):
        assert gate.shared_entries == 0
    complete = SimpleNamespace(hass=object(), entry=SimpleNamespace(entry_id="entry"), subentry=SimpleNamespace(subentry_id="agent"))
    async with agent_maintenance.conversation_request_lease(complete):
        assert gate.shared_entries == 1
''')
    s = remove_patch_call(s, '_install_conversation_guard')
    s = s.replace('["conversation", "backup", "legacy", "services"]', '["backup", "legacy", "services"]')
    write(path, s)

    path = 'tests/test_debug_coverage.py'
    s = Path(path).read_text()
    s = transform_function(s, 'test_install_debug_instrumentation_covers_lifecycle_and_phase_wrappers', lambda part: part.replace('    monkeypatch,\n', '    monkeypatch, entry_agent, entry_input,\n').replace('monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_process", process)', 'entry_agent._async_process_with_continuity = lambda request: process(entry_agent, request)').replace('hass = SimpleNamespace(data={})', 'hass = entry_agent.hass').replace('await wrapped_process(entity, user_input)', 'await entry_agent.async_process(entry_input)'))
    s = transform_function(s, 'test_traced_process_records_failure_and_resets_context', lambda part: part.replace('(monkeypatch)', '(monkeypatch, entry_agent, entry_input)').replace('monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_process", fail)', 'entry_agent._async_process_with_continuity = lambda request: fail(entry_agent, request)').replace('hass = SimpleNamespace(data={})', 'hass = entry_agent.hass').replace('await wrapped(entity, SimpleNamespace(conversation_id=None))', 'await entry_agent.async_process(entry_input)'))
    write(path, s)
    path = 'tests/test_debug_residual_coverage.py'
    write(path, remove_patch_call(Path(path).read_text(), '_async_process'))

    path = 'tests/test_conversation_cancellation_lifecycle.py'
    s = Path(path).read_text().replace('._async_process(', '._async_process_with_continuity(')
    write(path, s)
    path = 'tests/test_process_service.py'
    s = transform_function(Path(path).read_text(), 'test_early_failure_after_continuity_claim_releases_session', lambda part: part.replace('._async_process(', '._async_process_with_continuity('))
    write(path, s)
    path = 'tests/test_performance_coverage.py'
    s = Path(path).read_text().replace('inspect.unwrap(conversation.ExtendedOpenAIAgentEntity._async_process)', 'conversation.ExtendedOpenAIAgentEntity._async_process_with_continuity')
    s = s.replace('inspect.unwrap(ExtendedOpenAIAgentEntity._async_process)', 'ExtendedOpenAIAgentEntity._async_process_with_continuity')
    write(path, s)

    # Full public-entry doubles now need HA's data registry, just like production.
    path = 'tests/test_conversation_orchestration.py'
    s = Path(path).read_text().replace('hass=SimpleNamespace(bus=', 'hass=SimpleNamespace(data={}, bus=')
    s = s.replace('hass = SimpleNamespace(bus=', 'hass = SimpleNamespace(data={}, bus=')
    write(path, s)

print('Wrapper-specific regressions migrated to the real owner/helpers.')
