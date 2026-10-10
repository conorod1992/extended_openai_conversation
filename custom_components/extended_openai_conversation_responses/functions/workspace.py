"""Initialize the integration-owned workspace before File or Bash execution."""

from functools import partial
from pathlib import Path

from homeassistant.core import HomeAssistant


async def async_ensure_workspace(
    hass: HomeAssistant, path: Path, *, target: Path
) -> None:
    """Create the default workspace only when the operation uses it."""
    if not target.is_relative_to(path.resolve()):
        return
    await hass.async_add_executor_job(partial(path.mkdir, parents=True, exist_ok=True))
