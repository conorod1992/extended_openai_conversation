"""Serve the complete declared frontend through Home Assistant's actual HTTP app."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.management_loading_performance import (
    _asset_url,
    async_setup_cached_debug_ui,
    async_setup_cached_management_ui,
)
from homeassistant.core import HomeAssistant

from tests_real_ha.test_management_backend_acceptance import _entry, _setup_entry


@pytest.mark.asyncio
@pytest.mark.usefixtures("socket_enabled")  # The HTTP client starts a real loopback server.
async def test_startup_serves_all_assets_without_mutating_registry(
    hass: HomeAssistant, aiohttp_client: Any
) -> None:
    """Setup/repeated setup preserve the registry and serve lazy modules too."""
    modules = management_ui.MANAGEMENT_FRONTEND_MODULES
    await _setup_entry(hass, _entry("Frontend Registry Acceptance"))
    assert management_ui.MANAGEMENT_FRONTEND_MODULES is modules
    # Retain retry/idempotence behavior; do not register routes twice.
    await async_setup_cached_debug_ui(hass)
    await async_setup_cached_management_ui(hass)
    assert management_ui.MANAGEMENT_FRONTEND_MODULES is modules

    client = await aiohttp_client(hass.http.app)
    frontend = Path(management_ui.__file__).parent / "frontend"
    for name in (*modules, "debug-panel.js"):
        expected = await hass.async_add_executor_job((frontend / name).read_bytes)
        for url in (f"/{DOMAIN}/{name}", _asset_url(name)):
            response = await client.get(url)
            assert response.status == 200, url
            assert await response.read() == expected, url
    # Registry consolidation must not re-expose retired/retained legacy panels.
    for name in ("knowledge-panel.js", "memory-panel.js", "memory-management-panel.js"):
        for url in (f"/{DOMAIN}/{name}", _asset_url(name)):
            response = await client.get(url)
            # HA may serve its generic SPA shell for an unknown frontend URL;
            # neither that fallback nor a 404 exposes a legacy JavaScript asset.
            assert response.status == 404 or (
                response.status == 200 and response.content_type == "text/html"
            ), url
