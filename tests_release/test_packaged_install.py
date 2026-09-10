from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil

import pytest
from homeassistant.loader import async_get_integration

DOMAIN = "extended_openai_conversation_responses"


@pytest.mark.asyncio
async def test_packaged_release_is_discovered_and_imported_by_home_assistant(
    hass,
) -> None:
    """Install only the release payload into a clean HA config and load it."""
    source_value = os.environ.get("RELEASE_COMPONENT_DIR")
    expected_version = os.environ.get("RELEASE_EXPECTED_VERSION")
    assert source_value, "RELEASE_COMPONENT_DIR must point at the staged release payload"
    assert expected_version, "RELEASE_EXPECTED_VERSION must be set"

    source = Path(source_value).resolve()
    manifest_path = source / "manifest.json"
    assert manifest_path.is_file(), f"release payload is missing {manifest_path}"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert manifest["version"] == expected_version

    config_dir = Path(hass.config.config_dir).resolve()
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)

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
