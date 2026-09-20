"""Tests for the generated production frontend asset boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import frontend_assets
from custom_components.extended_openai_conversation_responses.const import DOMAIN


async def test_checked_in_production_entry_is_hashed_and_present(hass) -> None:
    hass.http.async_register_static_paths = AsyncMock()
    await frontend_assets.async_register_frontend_assets(hass)
    management = frontend_assets.frontend_entry_url(hass, "management")

    assert management.startswith(f"/{DOMAIN}/frontend/assets/management-")
    assert management.endswith(".js")

    relative = management.removeprefix(f"/{DOMAIN}/frontend/")
    assert (frontend_assets._PRODUCTION_DIR / relative).is_file()

    manifest = json.loads(frontend_assets._MANIFEST_PATH.read_text(encoding="utf-8"))
    debug_panel = next(
        entry
        for source, entry in manifest.items()
        if source.endswith("/debug-panel.js")
    )
    assert debug_panel.get("isDynamicEntry") is True
    assert str(debug_panel.get("file", "")).startswith("assets/debug-panel-")


async def test_frontend_entry_rejects_missing_manifest_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, hass
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source.js": {
                    "file": "assets/management-missing.js",
                    "name": "management",
                    "isEntry": True,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(frontend_assets, "_MANIFEST_PATH", manifest)
    monkeypatch.setattr(frontend_assets, "_PRODUCTION_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="management"):
        await frontend_assets.async_register_frontend_assets(hass)
    assert frontend_assets._FRONTEND_ENTRY_URLS not in hass.data
    assert frontend_assets._FRONTEND_ASSET_SETUP not in hass.data
    hass.http.async_register_static_paths.assert_not_called()


@pytest.mark.asyncio
async def test_register_frontend_assets_once_with_cache_headers() -> None:
    static_calls: list[list[Any]] = []

    async def register_static_paths(paths: list[Any]) -> None:
        static_calls.append(paths)

    hass = SimpleNamespace(
        data={},
        async_add_executor_job=AsyncMock(side_effect=lambda callback: callback()),
        http=SimpleNamespace(async_register_static_paths=register_static_paths),
    )

    await frontend_assets.async_register_frontend_assets(cast(Any, hass))
    await frontend_assets.async_register_frontend_assets(cast(Any, hass))

    assert len(static_calls) == 1
    assert len(static_calls[0]) == 1
    static_path = static_calls[0][0]
    assert static_path.url_path == f"/{DOMAIN}/frontend"
    assert Path(static_path.path) == frontend_assets._PRODUCTION_DIR
    assert static_path.cache_headers is True
