"""Tests for the generated production frontend asset boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.extended_openai_conversation_responses import frontend_assets
from custom_components.extended_openai_conversation_responses.const import DOMAIN


def test_checked_in_production_entry_is_hashed_and_present() -> None:
    management = frontend_assets.frontend_entry_url("management")

    assert management.startswith(f"/{DOMAIN}/frontend/assets/management-")
    assert management.endswith(".js")

    relative = management.removeprefix(f"/{DOMAIN}/frontend/")
    assert (frontend_assets._PRODUCTION_DIR / relative).is_file()

    manifest = frontend_assets._manifest()
    debug_panel = next(
        entry
        for source, entry in manifest.items()
        if source.endswith("/debug-panel.js")
    )
    assert debug_panel.get("isDynamicEntry") is True
    assert str(debug_panel.get("file", "")).startswith("assets/debug-panel-")


def test_frontend_entry_rejects_missing_manifest_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
        frontend_assets.frontend_entry_url("management")


@pytest.mark.asyncio
async def test_register_frontend_assets_once_with_cache_headers() -> None:
    static_calls: list[list[Any]] = []

    async def register_static_paths(paths: list[Any]) -> None:
        static_calls.append(paths)

    hass = SimpleNamespace(
        data={},
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
