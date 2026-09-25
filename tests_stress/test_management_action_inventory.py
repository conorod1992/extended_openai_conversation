"""Review every literal Management WebSocket section/action before shipping."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/extended_openai_conversation_responses"
INVENTORY = Path(__file__).with_name("management_action_inventory.json")


def _action_operand(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "action"
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "action"
    return False


def _actions(function: ast.AsyncFunctionDef) -> set[str]:
    found = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare) or not _action_operand(node.left):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                found.add(comparator.value)
            elif isinstance(comparator, (ast.Set, ast.Tuple)):
                found.update(
                    value.value for value in comparator.elts
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                )
    return found


def production_actions() -> dict[str, set[str]]:
    tree = ast.parse((COMPONENT / "management_ui.py").read_text(encoding="utf-8"))
    mapping = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_MANAGEMENT_SECTION_HANDLERS"
    )
    section_dict = mapping.value.args[0]
    assert isinstance(section_dict, ast.Dict)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)}
    sections = {
        key.value: _actions(functions[value.id])
        for key, value in zip(section_dict.keys, section_dict.values)
        if isinstance(key, ast.Constant) and isinstance(value, ast.Name)
    }
    assert len(sections) == len(section_dict.keys)
    for section, filename, function_name in (
        ("quiet_hours", "management_permissions.py", "async_quiet_hours_command"),
        ("function_repair", "management_function_repair.py", "async_function_repair"),
    ):
        extra = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
        function = next(node for node in extra.body if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name)
        sections[section] = _actions(function)
    # These endpoints use request.message["action"] and intentionally have no
    # local action variable. Keep their stable actions explicit.
    assert sections["scopes"] == {"catalog"}
    return sections


def test_management_actions_have_reviewed_evidence() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert inventory["schema_version"] == 1
    actual = production_actions()
    assert set(inventory["sections"]) == set(actual)
    for section, contract in inventory["sections"].items():
        assert set(contract["actions"]) == actual[section], (
            f"Management {section} actions changed: classify the new action and "
            f"add acceptance evidence in management_action_inventory.json"
        )
        evidence = contract["evidence"]
        assert evidence, section
        for reference in evidence:
            assert (ROOT / reference).is_file(), (section, reference)


def test_dummy_management_action_requires_review() -> None:
    actual = production_actions()
    assert "nightly_dummy" not in actual["configuration"]
    assert actual["configuration"] | {"nightly_dummy"} != set(
        json.loads(INVENTORY.read_text(encoding="utf-8"))["sections"]["configuration"]["actions"]
    )
