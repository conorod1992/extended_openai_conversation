"""Smoke-test the tracked release payload from a clean Home Assistant config."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

DOMAIN = "extended_openai_conversation_responses"

pytestmark = pytest.mark.skipif(
    not os.environ.get("RELEASE_COMPONENT_DIR"),
    reason="only enabled for packaged-release smoke runs",
)


@pytest.mark.asyncio
async def test_packaged_release_is_discovered_and_imported_by_home_assistant(
    hass: HomeAssistant,
) -> None:
    """Install only the staged release tree and make HA load it from that copy."""
    source = Path(os.environ["RELEASE_COMPONENT_DIR"]).resolve()
    expected_version = os.environ["RELEASE_EXPECTED_VERSION"]

    manifest_path = source / "manifest.json"
    assert manifest_path.is_file(), f"release payload is missing {manifest_path}"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert manifest["version"] == expected_version

    config_dir = Path(hass.config.config_dir).resolve()
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)

    # This test must prove that the clean installed copy is sufficient by itself.
    # Remove the checkout root from import resolution and evict any module that might
    # already have been imported while pytest collected the rest of the repository.
    repo_root = Path(__file__).parent.parent.resolve()
    sys.path[:] = [
        entry
        for entry in sys.path
        if not entry or Path(entry).resolve() != repo_root
    ]
    sys.path.insert(0, str(config_dir))
    for name in list(sys.modules):
        if name == f"custom_components.{DOMAIN}" or name.startswith(
            f"custom_components.{DOMAIN}."
        ):
            del sys.modules[name]
    importlib.invalidate_caches()

    integration = await async_get_integration(hass, DOMAIN)
    component = integration.get_component()

    assert integration.domain == DOMAIN
    assert integration.version == expected_version
    assert hasattr(component, "async_setup_entry")

    module = importlib.import_module(f"custom_components.{DOMAIN}")
    module_path = Path(module.__file__).resolve()
    assert destination in module_path.parents, (
        "Home Assistant imported the integration from the repository checkout "
        f"instead of the clean installed payload: {module_path}"
    )
