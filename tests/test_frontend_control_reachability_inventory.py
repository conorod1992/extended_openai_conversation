"""Tripwires for dynamically discovered frontend control reachability coverage."""

from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (
    ROOT / "custom_components" / "extended_openai_conversation_responses" / "frontend"
)
INVENTORY = ROOT / "tests_stress" / "frontend_control_reachability_inventory.json"
ROUTES = ROOT / "tests_stress" / "frontend_route_inventory.json"
SPEC = ROOT / "tests_browser" / "control-reachability.spec.mjs"
STRESS_CONFIG = ROOT / "playwright.stress.config.mjs"

INTERACTIVE_MARKER = re.compile(
    r"<(?:button|input|select|textarea|summary|a)\b"
    r"|<ha-selector\b"
    r"|role=[\"'](?:button|switch|checkbox|link|tab)[\"']"
)


def _payload() -> dict:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def _actual_source_markers() -> dict[str, int]:
    result: dict[str, int] = {}
    for path in sorted(FRONTEND.glob("*.js")):
        count = len(INTERACTIVE_MARKER.findall(path.read_text(encoding="utf-8")))
        if count:
            result[path.name] = count
    return result


def test_interactive_frontend_markup_requires_reviewed_inventory_update() -> None:
    """Adding/removing/moving interactive markup cannot silently escape review."""
    payload = _payload()
    actual = _actual_source_markers()

    assert payload["schema_version"] == 1
    assert actual == payload["files"], (
        "Interactive frontend markup changed. Update the reviewed reachability "
        "inventory and ensure the new/changed controls are exercised by the "
        "dynamic browser campaign."
    )
    assert sum(actual.values()) == payload["expected_total_markers"]


def test_reachability_campaign_covers_every_shipped_route_and_viewport_class() -> None:
    payload = _payload()
    route_payload = json.loads(ROUTES.read_text(encoding="utf-8"))
    source = SPEC.read_text(encoding="utf-8")

    assert "frontend_route_inventory.json" in source
    assert "frontend_control_reachability_inventory.json" in source
    assert set(route_payload["routes"])
    assert len(payload["viewports"]) >= 5
    widths = {item["width"] for item in payload["viewports"]}
    heights = {item["height"] for item in payload["viewports"]}
    assert min(widths) <= 360
    assert any(700 <= width <= 800 for width in widths)
    assert max(widths) >= 1400
    assert min(heights) <= 640

    for marker in (
        "scrollIntoViewIfNeeded",
        "boundingBox",
        "click({trial: true",
        "scrollWidth",
        "clientWidth",
        "details",
        "dialog",
        "popover",
    ):
        assert marker in source


def test_reachability_campaign_is_part_of_normal_and_nightly_browser_discovery() -> (
    None
):
    """The .spec file is normal CI-discovered and explicitly retained nightly."""
    spec = SPEC.read_text(encoding="utf-8")
    stress = STRESS_CONFIG.read_text(encoding="utf-8")
    assert (
        'test.describe("all rendered controls remain reachable across shipped routes and viewports"'
        in spec
    )
    assert '"control-reachability.spec.mjs"' in stress
