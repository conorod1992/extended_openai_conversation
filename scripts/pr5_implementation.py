"""Temporary, branch-only implementation transport; removed before the PR is ready."""
from pathlib import Path
import ast
import subprocess
import textwrap

ROOT = Path('custom_components/extended_openai_conversation_responses')
BASE = '4176cbb8011d7ce375750dd2fb0a321abb53a6f5'


def replace_once(text, before, after):
    assert text.count(before) == 1, (before, text.count(before))
    return text.replace(before, after, 1)


def replace_function(text, name, replacement=''):
    matches = [n for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(matches) == 1, (name, len(matches))
    node = matches[0]
    start = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
    lines = text.splitlines(keepends=True)
    lines[start:node.end_lineno] = [replacement.rstrip() + '\n'] if replacement else []
    return ''.join(lines)


def remove_entry_assignment(text):
    spans = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == '_async_process' for t in node.targets):
            spans.append((node.lineno - 1, node.end_lineno))
    assert len(spans) == 1, spans
    lines = text.splitlines(keepends=True)
    for start, end in reversed(spans):
        del lines[start:end]
    return ''.join(lines)


def original(name):
    path = ROOT / name
    baseline = subprocess.check_output(['git', 'show', f'{BASE}:{path}'], text=True)
    assert path.read_text() == baseline, f'Unreviewed concurrent edit: {path}'
    return baseline


def write(name, text):
    ast.parse(text)
    (ROOT / name).write_text(text)


if 'async def _async_process_with_continuity(' not in (ROOT / 'conversation.py').read_text():
    s = original('conversation.py')
    s = replace_once(s, 'from .agent_configuration import sync_memory_embedding_provider', '''from .agent_configuration import (
    async_reconcile_runtime_configuration,
    sync_memory_embedding_provider,
)
from .agent_maintenance import conversation_request_lease''')
    s = replace_once(s, 'from .debug import record_current_provider_failure', 'from .debug import conversation_debug_trace, record_current_provider_failure')
    s = replace_once(s, 'from .ha_tool_result_compat import', 'from .ha_permissions import bind_active_ha_context\nfrom .ha_tool_result_compat import')
    s = replace_once(s, 'from .scope import (', 'from .request_static_cache import formatted_tool_cache\nfrom .scope import (')
    s = replace_once(s, 'from .usage import async_get_usage', 'from .usage import async_get_usage\nfrom .voice_identity_runtime import voice_identity_scope')
    s = replace_function(s, '_async_process', textwrap.indent(textwrap.dedent('''
    async def _async_process(self, user_input: ConversationInput) -> ConversationResult:
        """Own the request boundary shared by Assist and direct processing.

        Preserve the effective order: HA caller, maintenance, capture, request cache,
        Voice Identity, live configuration, then continuity and processing. Installers
        must never replace this entry point or its request-entry owners.
        """
        with bind_active_ha_context(user_input.context):
            async with conversation_request_lease(self):
                with conversation_debug_trace(self, user_input) as trace:
                    with formatted_tool_cache():
                        async with voice_identity_scope(self, user_input):
                            await async_reconcile_runtime_configuration(self)
                            result = await self._async_process_with_continuity(user_input)
                    if trace is not None:
                        trace.result = result
                    return result

    async def _async_process_with_continuity(
        self, user_input: ConversationInput
    ) -> ConversationResult:
        """Resolve request policy/scope and release any claimed continuity turn."""
        cache_token = _PROMPT_CACHE_CONTEXT.set(None)
        try:
            llm_context = user_input.as_llm_context(DOMAIN)
            request_policy = self._resolve_live_guest_policy()
            guest_policy_token = _ACTIVE_GUEST_POLICY.set(request_policy)
            try:
                source_device_id = user_input.satellite_id or user_input.device_id
                scope = resolve_data_scope(
                    SimpleNamespace(context=llm_context.context, device_id=source_device_id),
                    self.subentry.data,
                )
                continuity_mode = self.subentry.data.get(
                    CONF_CONVERSATION_CONTINUITY, DEFAULT_CONVERSATION_CONTINUITY
                )
                timeout_minutes = int(self.subentry.data.get(
                    CONF_CONVERSATION_TIMEOUT_MINUTES, DEFAULT_CONVERSATION_TIMEOUT_MINUTES
                ))
                source_device_id = source_device_id or scope.device_id
                assert self._continuity is not None
                resolution = await self._continuity.async_resolve(
                    continuity_mode,
                    scope,
                    source_device_id,
                    user_input.conversation_id,
                    timeout_minutes,
                    namespace=GUEST_CONTINUITY_NAMESPACE if request_policy.guest_active else None,
                )
                try:
                    return await self._async_process_claimed(
                        user_input, llm_context, request_policy, scope,
                        source_device_id, timeout_minutes, resolution,
                    )
                finally:
                    await asyncio.shield(self._continuity.async_release(
                        resolution.key, resolution.claim_token
                    ))
            finally:
                _ACTIVE_GUEST_POLICY.reset(guest_policy_token)
        finally:
            _PROMPT_CACHE_CONTEXT.reset(cache_token)
    ''').strip(), '    '))
    write('conversation.py', s)

    s = original('voice_identity_runtime.py')
    s = s.replace('from collections.abc import Iterator, Mapping', 'from collections.abc import AsyncIterator, Iterator, Mapping')
    s = s.replace('from contextlib import contextmanager', 'from contextlib import asynccontextmanager, contextmanager')
    s = s.replace('from functools import wraps\n', '').replace('_INSTALLED = False\n', '')
    s = replace_function(s, 'install_voice_identity_runtime', '''@asynccontextmanager
async def voice_identity_scope(agent: Any, user_input: Any) -> AsyncIterator[None]:
    """Validate data ownership using the registry source and restore all context.

    Voice Identity never replaces the authenticated HA caller used for permissions.
    """
    with _prefer_registry_device_source(user_input):
        active_users = await _active_configured_users(agent, user_input)
        with bind_active_voice_identity_users(active_users):
            yield
''')
    write('voice_identity_runtime.py', s)

    s = original('ha_permissions.py')
    s = s.replace('from functools import wraps\n', '').replace('from homeassistant.components.conversation import ConversationInput, ConversationResult\n', '')
    s = replace_function(s, '_install_request_context_binding')
    start = s.index('    # This setup runs after')
    end = s.index('    cache: dict[str, Any]', start)
    s = s[:start] + '    # The conversation owner binds caller context; setup maintains the user cache.\n' + s[end:]
    write('ha_permissions.py', s)

    s = original('agent_maintenance.py')
    s = replace_function(s, '_install_conversation_guard', '''@asynccontextmanager
async def conversation_request_lease(entity: Any) -> AsyncIterator[None]:
    """Hold one agent lease across preparation, processing and result capture."""
    entry = getattr(entity, "entry", None)
    subentry = getattr(entity, "subentry", None)
    hass = getattr(entity, "hass", None)
    entry_id = getattr(entry, "entry_id", None)
    subentry_id = getattr(subentry, "subentry_id", None)
    if hass is None or not isinstance(entry_id, str) or not isinstance(subentry_id, str):
        yield
        return
    async with get_agent_maintenance_gate(hass, entry_id, subentry_id).shared():
        yield
''')
    s = replace_once(s, '    _install_conversation_guard()\n', '')
    write('agent_maintenance.py', s)

    s = original('configuration_lifecycle_hardening.py')
    s = s.replace('    async_reconcile_runtime_configuration,\n', '')
    s = replace_once(s, '    original_process = ExtendedOpenAIAgentEntity._async_process\n', '')
    s = replace_function(s, 'async_process')
    s = remove_entry_assignment(s)
    s = s.replace('Request-boundary configuration guards retained with the processing stack.', 'Live capability and memory-provider guards retained with the processing stack.')
    s = s.replace('Reconcile optional runtime managers at request boundaries and live gates.', 'Retain live capability gates at memory retrieval and execution boundaries.')
    write('configuration_lifecycle_hardening.py', s)

    s = original('request_static_cache.py')
    s = replace_once(s, 'from __future__ import annotations\n', 'from __future__ import annotations\n\nfrom collections.abc import Iterator\nfrom contextlib import contextmanager\n')
    s = replace_once(s, '    original_process = conversation.ExtendedOpenAIAgentEntity._async_process\n', '')
    s = replace_function(s, 'process_with_fresh_formatted_tool_cache')
    s = remove_entry_assignment(s)
    s = replace_once(s, 'def install_request_static_caching() -> None:', '''@contextmanager
def formatted_tool_cache() -> Iterator[None]:
    """Give one request a fresh formatted-tool cache and restore the prior cache."""
    token = _FORMATTED_TOOLS.set({})
    try:
        yield
    finally:
        _FORMATTED_TOOLS.reset(token)


def install_request_static_caching() -> None:''')
    write('request_static_cache.py', s)

    s = original('debug.py')
    s = replace_once(s, 'from __future__ import annotations\n', 'from __future__ import annotations\n\nfrom collections.abc import Iterator\nfrom contextlib import contextmanager\n')
    s = replace_once(s, '    original_process = ExtendedOpenAIAgentEntity._async_process\n', '')
    s = replace_function(s, 'traced_process')
    s = remove_entry_assignment(s)
    s = replace_once(s, 'def install_debug_instrumentation() -> None:', '''@contextmanager
def conversation_debug_trace(agent: Any, user_input: Any) -> Iterator[DebugTrace | None]:
    """Capture one opted-in request without replacing its owner or inner phases."""
    manager = get_debug_manager(agent.hass, agent.entry.entry_id, agent.subentry.subentry_id)
    if not manager.enabled:
        yield None
        return
    trace = manager.begin(
        entry_id=agent.entry.entry_id,
        subentry_id=agent.subentry.subentry_id,
        user_input=user_input,
        incoming_conversation_id=user_input.conversation_id,
    )
    token = _ACTIVE_DEBUG_TRACE.set(trace)
    try:
        yield trace
    except BaseException as err:
        manager.finish(trace, successful=False, error=err)
        raise
    else:
        manager.finish(trace, successful=trace.error_type is None, result=trace.result)
    finally:
        _ACTIVE_DEBUG_TRACE.reset(token)


def install_debug_instrumentation() -> None:''')
    s = s.replace('Install lightweight hooks once; they are inert until debug capture is enabled.', 'Instrument inner phases once; request capture is owned by conversation.')
    write('debug.py', s)

    s = original('__init__.py')
    s = replace_once(s, 'from .voice_identity_runtime import install_voice_identity_runtime\n', '')
    s = replace_once(s, '    install_voice_identity_runtime()\n', '')
    s = s.replace('# Gate the remaining effective conversation and service entry points.', '# Install the remaining backup, legacy Memory and service maintenance gates.')
    write('__init__.py', s)

# The workbench must never broaden into inner request/runtime or frontend ownership.
for name in ('conversation.py', 'voice_identity_runtime.py', 'ha_permissions.py', 'agent_maintenance.py', 'configuration_lifecycle_hardening.py', 'request_static_cache.py', 'debug.py', '__init__.py'):
    ast.parse((ROOT / name).read_text())
print('Reviewed request-entry implementation applied.')
