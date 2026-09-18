"""Schedule low-frequency Archive retention maintenance."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_ARCHIVE_RETENTION_DAYS, DEFAULT_ARCHIVE_RETENTION_DAYS

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ARCHIVE_RETENTION_INTERVAL = timedelta(days=1)


async def async_prune_archive_retention(
    agent: Any, _now: datetime | None = None
) -> None:
    """Enforce the current Archive retention setting during long HA uptimes."""
    archive = getattr(agent, "_archive", None)
    if archive is None:
        return
    try:
        await archive.async_prune(
            int(
                agent.subentry.data.get(
                    CONF_ARCHIVE_RETENTION_DAYS, DEFAULT_ARCHIVE_RETENTION_DAYS
                )
            )
        )
    except Exception:
        _LOGGER.exception(
            "Background conversation archive retention maintenance failed"
        )


def _install_archive_retention_schedule() -> None:
    """Schedule low-frequency Archive retention independently of conversation traffic."""
    from .conversation import ExtendedOpenAIAgentEntity

    agent_type: Any = ExtendedOpenAIAgentEntity
    current = agent_type.async_added_to_hass
    if getattr(current, "_extended_openai_archive_retention", False):
        return
    original = current

    async def async_added_to_hass(agent: Any) -> None:
        await original(agent)

        async def prune(now: datetime) -> None:
            await async_prune_archive_retention(agent, now)

        agent.async_on_remove(
            async_track_time_interval(
                agent.hass,
                prune,
                _ARCHIVE_RETENTION_INTERVAL,
            )
        )

    async_added_to_hass._extended_openai_archive_retention = True  # type: ignore[attr-defined]
    agent_type.async_added_to_hass = async_added_to_hass


def install_durable_state_hardening() -> None:
    """Install Archive retention maintenance once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_archive_retention_schedule()
    _INSTALLED = True
