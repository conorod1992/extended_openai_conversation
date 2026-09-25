"""Reviewed acceptance evidence for every EOAI-owned HA service."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/extended_openai_conversation_responses"
SOURCES = ("services.py", "intercom_services.py", "quiet_hours.py")
INVENTORY = Path(__file__).with_name("public_service_inventory.json")


def registered_services() -> set[str]:
    values = {}
    for filename in ("const.py", *SOURCES):
        tree = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                values.update(
                    {target.id: node.value.value for target in node.targets if isinstance(target, ast.Name)}
                )
    services = set()
    for filename in SOURCES:
        tree = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "async_register" or len(node.args) < 2:
                continue
            if not isinstance(node.args[0], ast.Name) or node.args[0].id != "DOMAIN":
                continue
            name = node.args[1]
            if isinstance(name, ast.Constant):
                services.add(name.value)
            elif isinstance(name, ast.Name):
                assert name.id in values, (filename, name.id)
                services.add(values[name.id])
            else:
                raise AssertionError(f"Unclassified service registration in {filename}")
    return services


def test_all_public_services_have_acceptance_evidence() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert inventory["schema_version"] == 1
    actual = registered_services()
    assert set(inventory["services"]) == actual, (
        f"Public services changed: new={sorted(actual - set(inventory['services']))}, "
        f"removed={sorted(set(inventory['services']) - actual)}"
    )
    for name, paths in inventory["services"].items():
        assert paths, name
        for path in paths:
            assert (ROOT / path).is_file(), (name, path)


def test_new_public_service_fails_inventory_contract() -> None:
    assert "nightly_dummy" not in registered_services()
    assert "nightly_dummy" not in json.loads(INVENTORY.read_text(encoding="utf-8"))["services"]
