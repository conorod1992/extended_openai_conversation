"""Initialize the integration-owned workspace before File or Bash execution."""

from functools import partial
from pathlib import Path

from homeassistant.core import HomeAssistant


async def async_ensure_workspace(hass: HomeAssistant, path: Path) -> None:
    """Create the default workspace outside the event loop, also on fresh installs."""
    await hass.async_add_executor_job(partial(path.mkdir, parents=True, exist_ok=True))
