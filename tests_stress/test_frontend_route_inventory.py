"""Nightly tripwire for newly shipped management routes."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js"
INVENTORY = ROOT / "tests_stress/frontend_route_inventory.json"
LEVELS = {"full-crud", "read-write", "read-only", "render-navigation"}


def shipped_routes(source: str) -> set[str]:
    """Read the canonical NAVIGATION declaration, ignoring legacy redirects."""
    navigation = source.split("const LEGACY_ROUTES", 1)[0]
    pages = re.findall(r'^  \{id: "([^"]+)", label: [^\n]* sections: \[', navigation, re.M)
    routes = set()
    for block in re.finditer(
        r'^  \{id: "([^"]+)", label: [^\n]* sections: \[\n(.*?)^  \]\},',
        navigation,
        re.M | re.S,
    ):
        page, body = block.groups()
        assert page in pages
        routes.update(f"{page}/{section}" for section in re.findall(r'\{id: "([^"]+)"', body))
    routes.update(re.findall(r'^  \{id: "([^"]+)", label: [^\n]* sections: \[\]\},', navigation, re.M))
    return routes


def test_every_frontend_route_has_reviewed_acceptance_level() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert inventory["schema_version"] == 1
    actual = shipped_routes(SOURCE.read_text(encoding="utf-8"))
    classified = inventory["routes"]
    assert set(classified) == actual, (
        f"Unclassified or removed frontend routes: "
        f"new={sorted(actual - set(classified))}, removed={sorted(set(classified) - actual)}"
    )
    assert set(classified.values()) <= LEVELS


def test_new_route_fails_inventory_contract() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    dummy = source.replace(
        '{id: "guide", label: "Guide", path:',
        '{id: "nightly-dummy", label: "Dummy", path: "/extended-openai/nightly-dummy", sections: []},\n  {id: "guide", label: "Guide", path:',
        1,
    )
    assert "nightly-dummy" in shipped_routes(dummy)
    assert "nightly-dummy" not in json.loads(INVENTORY.read_text(encoding="utf-8"))["routes"]
