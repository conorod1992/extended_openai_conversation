"""Temporary, branch-local Management ownership edit workbench."""
from pathlib import Path
import ast
import hashlib
import textwrap

p = Path('custom_components/extended_openai_conversation_responses/management_ui.py')
s = p.read_text()
if 'class _ManagementRequest:' not in s:
    lines = s.splitlines(keepends=True)
    tree = ast.parse(s)
    f = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'async_management_command')
    branches = [n for n in f.body if isinstance(n, ast.If) and 'section' in ast.unparse(n.test)]
    fields = {'hass': 'request.hass', 'user_id': 'request.user_id', 'is_admin': 'request.is_admin', 'message': 'request.message', 'entry': 'request.entry', 'subentry': 'request.subentry', 'entry_id': 'request.entry_id', 'subentry_id': 'request.subentry_id', 'action': 'request.message["action"]', 'scope_id': '_selected_scope(request.user_id, request.is_admin, request.message.get("scope_id"))'}
    handlers = []
    names = []
    for branch in branches:
        condition = branch.test
        first = condition.values[0] if isinstance(condition, ast.BoolOp) else condition
        section = first.comparators[0].value
        names.append(section)
        body = textwrap.dedent(''.join(lines[branch.body[0].lineno - 1:branch.end_lineno]))
        if isinstance(condition, ast.BoolOp):
            body = 'if ' + ast.unparse(condition.values[1]) + ':\n' + textwrap.indent(body, '    ')
        used = {n.id for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        scoped = section in {'conversations', 'memories', 'knowledge', 'settings'}
        pre = ''
        for name, expr in fields.items():
            if name in used or (name == 'scope_id' and scoped):
                pre += f'    {name} = {expr}\n' if name in used else f'    {expr}\n'
        handlers.append(f'async def async_{section}_command(request: _ManagementRequest) -> dict[str, Any]:\n    """Handle the {section.replace("_", " ")} Management section."""\n' + pre + textwrap.indent(body, '    ') + '\n    return _unknown_management_action(request)\n')
    head = ''.join(lines[f.lineno - 1:906])
    head = head.replace('    entry, subentry = entry_and_agent(hass, entry_id, subentry_id)\n', '')
    head = head.rstrip() + '\n    entry, subentry = entry_and_agent(hass, entry_id, subentry_id)\n'
    head += '    request = _ManagementRequest(hass, user_id, is_admin, message, entry_id, subentry_id, entry, subentry)\n    handler = _MANAGEMENT_SECTION_HANDLERS.get(section)\n    if handler is None:\n        return _unknown_management_action(request)\n    return await handler(request)\n'
    ctx = '''@dataclass(frozen=True)
class _ManagementRequest:
    """One validated agent selection shared by explicit section handlers."""

    hass: HomeAssistant
    user_id: str
    is_admin: bool
    message: dict[str, Any]
    entry_id: str
    subentry_id: str
    entry: Any
    subentry: ConfigSubentry


def _unknown_management_action(request: _ManagementRequest) -> dict[str, Any]:
    # Preserve the existing scope boundary on legacy fall-through errors.
    _selected_scope(request.user_id, request.is_admin, request.message.get("scope_id"))
    raise HomeAssistantError(
        f"Unknown {request.message.get('section', 'overview')} management action: {request.message['action']}"
    )


'''
    registry = '_MANAGEMENT_SECTION_HANDLERS: Final = MappingProxyType({\n' + ''.join(f'    "{n}": async_{n}_command,\n' for n in names) + '})\n\n\n'
    new = ctx + '\n\n'.join(handlers) + '\n\n' + registry + head
    s = ''.join(lines[:f.lineno - 1]) + new + ''.join(lines[f.end_lineno:])
    s = s.replace('from collections.abc import Mapping\n', 'from collections.abc import Mapping\nfrom dataclasses import dataclass\n')
    ast.parse(s)
    assert hashlib.sha256(s.encode()).hexdigest() == '68bd7e584ea1bb1cc01726f7bdb4025fdd3efde13474e52850772f613681a29e', 'Edit does not match reviewed local source'
    p.write_text(s)
