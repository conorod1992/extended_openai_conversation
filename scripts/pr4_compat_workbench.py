"""Replace deprecated result reads with the explicit HA compatibility boundary."""
import ast
from pathlib import Path
root = Path.cwd()
production = root / 'custom_components/extended_openai_conversation_responses'
for directory in [production, root / 'tests', root / 'tests_real_ha']:
    for path in sorted(directory.rglob('*.py')):
        if path.name == 'ha_tool_result_compat.py':
            continue
        source = path.read_text()
        tree = ast.parse(source)
        raw = source.encode()
        lines = raw.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        edits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == 'tool_result' and isinstance(node.ctx, ast.Load):
                value = ast.get_source_segment(source, node.value)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == 'getattr' and len(node.args) == 3
                  and isinstance(node.args[1], ast.Constant) and node.args[1].value == 'tool_result'
                  and isinstance(node.args[2], ast.Constant) and node.args[2].value is None):
                value = ast.get_source_segment(source, node.args[0])
            else:
                continue
            edits.append((offsets[node.lineno-1] + node.col_offset,
                          offsets[node.end_lineno-1] + node.end_col_offset,
                          f'tool_result_data({value})'.encode()))
        if not edits:
            continue
        ordered = sorted(edits)
        assert all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])), path
        for start, end, value in reversed(ordered):
            raw = raw[:start] + value + raw[end:]
        source = raw.decode()
        tree = ast.parse(source)
        prologue = 0
        if isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
            prologue = tree.body[0].end_lineno
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == '__future__':
                prologue = node.end_lineno
        lines = source.splitlines(keepends=True)
        module = '.ha_tool_result_compat' if path.is_relative_to(production) else 'custom_components.extended_openai_conversation_responses.ha_tool_result_compat'
        lines.insert(prologue, f'\nfrom {module} import tool_result_data\n')
        output = ''.join(lines)
        ast.parse(output)
        path.write_text(output)
        print(path.relative_to(root), len(edits))

helper = production / "ha_tool_result_compat.py"
helper.write_text(helper.read_text() + '\n\n_MISSING_RESULT = object()\n\n\ndef tool_result_data(content: Any, default: Any = None) -> Any:\n    """Read a chat-log result without accessing HA\'s deprecated compatibility property.\n\n    The fallback must be lazy: newer HA still exposes ``tool_result``, but reading\n    it reports deprecated usage. Return the original data, not a copy, so existing\n    result projections can continue to compact their owned payload in place.\n    """\n    result = getattr(content, "result", _MISSING_RESULT)\n    if result is not _MISSING_RESULT:\n        return unwrap_tool_result(result)\n    return getattr(content, "tool_result", default)\n')

services = production / "services.py"
services.write_text(services.read_text().replace('        if hasattr(result, "tool_result"):\n            result = tool_result_data(result)\n', '        result = tool_result_data(result, default=result)\n'))

tests = root / "tests/test_ha_tool_result_compat.py"
tests.write_text(tests.read_text() + '\n\ndef test_tool_result_data_round_trips_the_installed_ha_api() -> None:\n    """The real HA content type preserves the exact data object, old or new."""\n    payload = {"result": {"nested": [1, {"message": "café"}]}}\n    content = ha_tool_result_compat.make_tool_result_content(\n        agent_id="conversation.test",\n        tool_call_id="roundtrip",\n        tool_name="lookup",\n        tool_result=payload,\n    )\n    assert ha_tool_result_compat.tool_result_data(content) is payload\n\n\ndef test_tool_result_data_legacy_and_absent_values() -> None:\n    """Older result content, optional results and raw service values still work."""\n    from types import SimpleNamespace\n\n    payload = {"result": "old"}\n    assert (\n        ha_tool_result_compat.tool_result_data(SimpleNamespace(tool_result=payload))\n        is payload\n    )\n    for value in (None, {}, [], "", 0, False):\n        assert (\n            ha_tool_result_compat.tool_result_data(SimpleNamespace(tool_result=value))\n            is value\n        )\n        assert (\n            ha_tool_result_compat.tool_result_data(\n                SimpleNamespace(result=value, tool_result="wrong")\n            )\n            is value\n        )\n    raw_service_result = {"answer": 42}\n    assert ha_tool_result_compat.tool_result_data(raw_service_result) is None\n    assert (\n        ha_tool_result_compat.tool_result_data(\n            raw_service_result, default=raw_service_result\n        )\n        is raw_service_result\n    )\n\n\ndef test_tool_result_data_never_reads_new_api_deprecated_property(\n    monkeypatch: Any,\n) -> None:\n    """Do not evaluate the legacy property even as an eager getattr default."""\n    from custom_components.extended_openai_conversation_responses import (\n        model_tool_results,\n        request_diagnostics,\n    )\n\n    @dataclass\n    class ModernResult:\n        data: dict[str, Any]\n\n    class ModernContent:\n        def __init__(self, payload: dict[str, Any]) -> None:\n            self.result = ModernResult(payload)\n\n        @property\n        def tool_result(self) -> Any:\n            raise AssertionError("deprecated property must never be accessed")\n\n    monkeypatch.setattr(\n        ha_tool_result_compat.llm, "ToolResult", ModernResult, raising=False\n    )\n    payload = {"result": \'{ "a": 1, "b": [2, 3] }\'}\n    content = ModernContent(payload)\n    assert ha_tool_result_compat.tool_result_data(content) is payload\n    assert request_diagnostics._result_characters(content) == len(payload["result"])\n    assert model_tool_results._compact_json_result_content(content) is content\n    assert payload["result"] == \'{"a":1,"b":[2,3]}\'\n    assert content.result.data is payload\n\n\ndef test_tool_result_data_does_not_hide_result_access_failures() -> None:\n    """Compatibility is not a blanket error/warning suppression layer."""\n    import pytest\n\n    class BrokenContent:\n        @property\n        def result(self) -> Any:\n            raise RuntimeError("unexpected result failure")\n\n    with pytest.raises(RuntimeError, match="unexpected result failure"):\n        ha_tool_result_compat.tool_result_data(BrokenContent())\n\n\ndef test_production_tool_result_reads_stay_centralized() -> None:\n    """All production legacy reads/probes must stay behind the version boundary."""\n    import ast\n\n    root = (\n        Path(__file__).parents[1]\n        / "custom_components"\n        / "extended_openai_conversation_responses"\n    )\n    offenders: list[str] = []\n    for path in root.rglob("*.py"):\n        if path.name == "ha_tool_result_compat.py":\n            continue\n        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):\n            direct = isinstance(node, ast.Attribute) and node.attr == "tool_result"\n            probe = (\n                isinstance(node, ast.Call)\n                and isinstance(node.func, ast.Name)\n                and node.func.id in {"getattr", "hasattr"}\n                and len(node.args) >= 2\n                and isinstance(node.args[1], ast.Constant)\n                and node.args[1].value == "tool_result"\n            )\n            if direct or probe:\n                offenders.append(f"{path.relative_to(root)}:{node.lineno}")\n    assert offenders == []\n')

import hashlib
import subprocess
import sys
subprocess.run([sys.executable, "-m", "ruff", "check", "--fix", "custom_components"], check=True)
changed = subprocess.check_output(["git", "diff", "--name-only", "--", "tests", "tests_real_ha"], text=True).splitlines()
subprocess.run([sys.executable, "-m", "ruff", "check", "--select", "I", "--fix", *changed], check=True)
subprocess.run([sys.executable, "-m", "ruff", "format", "custom_components", "tests/test_ha_tool_result_compat.py"], check=True)
patch = subprocess.check_output(["git", "diff", "--binary", "--", "custom_components", "tests", "tests_real_ha"])
assert hashlib.sha256(patch).hexdigest() == "17481092c2be644ac834701142b2185e722810090082de8e92793035c790d66a", "Output differs from the locally tested patch"
subprocess.run(["git", "diff", "--check"], check=True)
