"""Production frontend asset manifest and static registration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_FRONTEND_ASSET_SETUP: Final = f"{DOMAIN}.frontend_asset_setup"
_FRONTEND_ENTRY_URLS: Final = f"{DOMAIN}.frontend_entry_urls"
_PRODUCTION_DIR: Final = Path(__file__).parent / "frontend" / "dist"
_MANIFEST_PATH: Final = _PRODUCTION_DIR / "manifest.json"
_ASSET_URL_PREFIX: Final = f"/{DOMAIN}/frontend"


def _load_entry_urls_sync() -> dict[str, str]:
    """Load and validate Vite entry URLs off the Home Assistant event loop."""
    try:
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise RuntimeError("Production frontend manifest is unavailable") from err
    if not isinstance(data, dict):
        raise RuntimeError("Production frontend manifest is invalid")

    urls: dict[str, str] = {}
    for entry in data.values():
        if not isinstance(entry, dict) or entry.get("isEntry") is not True:
            continue
        name = entry.get("name")
        filename = entry.get("file")
        if not isinstance(name, str) or not isinstance(filename, str):
            continue
        if not filename.startswith("assets/"):
            raise RuntimeError(f"Production frontend entry is invalid: {name}")
        candidate = _PRODUCTION_DIR / filename
        if not candidate.is_file():
            raise RuntimeError(f"Production frontend entry is unavailable: {name}")
        urls[name] = f"{_ASSET_URL_PREFIX}/{filename}"

    if "management" not in urls:
        raise RuntimeError("Production frontend entry is unavailable: management")
    return urls


def frontend_entry_url(hass: HomeAssistant, name: str) -> str:
    """Return one previously validated Vite entry URL from memory."""
    urls = hass.data.get(_FRONTEND_ENTRY_URLS)
    if not isinstance(urls, dict):
        raise RuntimeError("Production frontend assets are not registered")
    url = urls.get(name)
    if not isinstance(url, str):
        raise RuntimeError(f"Production frontend entry is unavailable: {name}")
    return url


async def async_register_frontend_assets(hass: HomeAssistant) -> None:
    """Register generated assets after one executor-backed manifest validation."""
    if hass.data.get(_FRONTEND_ASSET_SETUP):
        return

    entry_urls = await hass.async_add_executor_job(_load_entry_urls_sync)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                _ASSET_URL_PREFIX,
                str(_PRODUCTION_DIR),
                cache_headers=True,
            )
        ]
    )
    hass.data[_FRONTEND_ENTRY_URLS] = entry_urls
    hass.data[_FRONTEND_ASSET_SETUP] = True
