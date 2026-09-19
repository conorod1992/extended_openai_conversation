"""Run the explicitly reviewed PR5 edits only on their isolated branch."""
from pathlib import Path
import ast
import runpy
import subprocess

branch = subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip()
assert not branch or branch == 'refactor/conversation-entry-ownership', branch
runpy.run_path('scripts/pr5_implementation.py', run_name='__main__')
runpy.run_path('scripts/pr5_test_migration.py', run_name='__main__')


def replace_checked(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new, 1)


def write(path, text):
    ast.parse(text)
    path.write_text(text)


# Keep all actual setup/installers active; fake only the external HTTP/panel effects.
p = Path('tests/test_conversation_entry_ownership.py')
s = p.read_text()
if 'hass.http.async_register_static_paths = AsyncMock()' not in s:
    s = replace_checked(s, '    hass.data = {}\n', '    hass.data = {}\n    hass.http.async_register_static_paths = AsyncMock()\n')
    s = replace_checked(s, '    with ExitStack() as stack:\n', '    with ExitStack() as stack:\n        stack.enter_context(patch("homeassistant.components.panel_custom.async_register_panel", AsyncMock()))\n')
    write(p, s)

# The real public owner prefers a registry device; without it the satellite survives.
p = Path('tests/test_conversation_orchestration.py')
s = p.read_text()
if '@pytest.mark.parametrize("device", [None, "kitchen"])' not in s:
    s = replace_checked(s,
        '@pytest.mark.parametrize("satellite", [None, "assist.kitchen"])\n',
        '@pytest.mark.parametrize("satellite", [None, "assist.kitchen"])\n@pytest.mark.parametrize("device", [None, "kitchen"])\n')
    s = replace_checked(s, '    monkeypatch, mode, guest, satellite\n', '    monkeypatch, mode, guest, satellite, device\n')
    s = replace_checked(s,
        '    await invoke(satellite=satellite, incoming="incoming-ha-id")\n',
        '    await invoke(device=device, satellite=satellite, incoming="incoming-ha-id")\n')
    s = replace_checked(s,
        '    assert args[1].device_id == (satellite or "kitchen")\n    assert args[2:] == (satellite or "kitchen", "incoming-ha-id", 37)\n',
        '    expected_device = device or satellite\n    assert args[1].device_id == expected_device\n    assert args[2:] == (expected_device, "incoming-ha-id", 37)\n')
    write(p, s)

# This existing test intentionally targets prompt-context setup before any claim.
# The new full-entry matrix independently verifies the enclosing resource lifetimes.
p = Path('tests/test_performance_coverage.py')
s = p.read_text()
if 'await unwrap(ExtendedOpenAIAgentEntity._async_process)(' in s:
    s = replace_checked(s, '    from inspect import unwrap\n\n', '')
    s = replace_checked(s,
        'await unwrap(ExtendedOpenAIAgentEntity._async_process)(',
        'await ExtendedOpenAIAgentEntity._async_process_with_continuity(')
    write(p, s)

print('Inspected unit-fixture failures corrected; production code is unchanged.')
