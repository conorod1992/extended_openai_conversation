"""Serve the complete bundled frontend through Home Assistant's actual HTTP app."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import frontend_assets
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.management_loading_performance import (
    async_setup_cached_debug_ui,
    async_setup_cached_management_ui,
)
from homeassistant.core import HomeAssistant

from tests_real_ha.test_management_backend_acceptance import _entry, _setup_entry


@pytest.mark.asyncio
@pytest.mark.usefixtures("socket_enabled")  # The HTTP client starts a real loopback server.
async def test_startup_serves_bundled_assets_idempotently(
    hass: HomeAssistant, aiohttp_client: Any
) -> None:
    """Repeated setup preserves one production asset root and serves every chunk."""
    await _setup_entry(hass, _entry("Frontend Bundle Acceptance"))
    await async_setup_cached_debug_ui(hass)
    await async_setup_cached_management_ui(hass)

    client = await aiohttp_client(hass.http.app)
    manifest = frontend_assets._manifest()
    files = {entry["file"] for entry in manifest.values()}

    for filename in files:
        expected = await hass.async_add_executor_job(
            (frontend_assets._PRODUCTION_DIR / filename).read_bytes
        )
        url = f"{frontend_assets._ASSET_URL_PREFIX}/{filename}"
        response = await client.get(url)
        assert response.status == 200, url
        assert await response.read() == expected, url

    management_url = frontend_assets.frontend_entry_url("management")
    response = await client.get(management_url)
    assert response.status == 200
    assert response.content_type in {"text/javascript", "application/javascript"}

    # Raw source modules are build inputs, not production HTTP assets.
    for name in ("management-panel.js", "debug-panel.js", "knowledge-panel.js"):
        response = await client.get(f"/{DOMAIN}/{name}")
        assert response.status == 404 or (
            response.status == 200 and response.content_type == "text/html"
        ), name
