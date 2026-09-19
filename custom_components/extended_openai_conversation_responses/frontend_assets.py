"""Production frontend asset manifest and static registration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_FRONTEND_ASSET_SETUP: Final = f"{DOMAIN}.frontend_asset_setup"
_PRODUCTION_DIR: Final = Path(__file__).parent / "frontend" / "dist"
_MANIFEST_PATH: Final = _PRODUCTION_DIR / "manifest.json"
_ASSET_URL_PREFIX: Final = f"/{DOMAIN}/frontend"


def _manifest() -> dict[str, dict[str, object]]:
    """Load the checked-in Vite production manifest."""
    try:
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise RuntimeError("Production frontend manifest is unavailable") from err
    if not isinstance(data, dict):
        raise RuntimeError("Production frontend manifest is invalid")
    return data


def frontend_entry_url(name: str) -> str:
    """Return the versioned URL for one named Vite entry."""
    for entry in _manifest().values():
        if not isinstance(entry, dict):
            continue
        if entry.get("isEntry") is not True or entry.get("name") != name:
            continue
        filename = entry.get("file")
        if not isinstance(filename, str) or not filename.startswith("assets/"):
            break
        candidate = _PRODUCTION_DIR / filename
        if not candidate.is_file():
            break
        return f"{_ASSET_URL_PREFIX}/{filename}"
    raise RuntimeError(f"Production frontend entry is unavailable: {name}")


async def async_register_frontend_assets(hass: HomeAssistant) -> None:
    """Register the generated production directory once with immutable caching."""
    if hass.data.get(_FRONTEND_ASSET_SETUP):
        return
    # Resolve both public entries before exposing the static directory so a
    # partial or stale build fails setup deterministically.
    frontend_entry_url("management")
    frontend_entry_url("debug")
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                _ASSET_URL_PREFIX,
                str(_PRODUCTION_DIR),
                cache_headers=True,
            )
        ]
    )
    hass.data[_FRONTEND_ASSET_SETUP] = True
