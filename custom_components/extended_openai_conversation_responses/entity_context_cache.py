"""Cache registry-derived entity prompt metadata with live invalidation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.template.helpers import resolve_area_id

from .const import DOMAIN

_CACHE_KEY = f"{DOMAIN}.entity_prompt_metadata_cache"


@dataclass(frozen=True, slots=True)
class EntityPromptMetadata:
    """Registry-derived metadata that is static between registry updates."""

    aliases: tuple[str, ...]
    area_id: str | None


def normalize_entity_aliases(aliases: list[Any] | tuple[Any, ...] | None) -> list[str]:
    """Return only genuine string aliases, excluding HA's computed-name sentinel."""
    if not aliases:
        return []
    return [alias for alias in aliases if isinstance(alias, str)]


def _build_metadata(hass: HomeAssistant, entity_id: str) -> EntityPromptMetadata:
    entity = er.async_get(hass).async_get(entity_id)
    return EntityPromptMetadata(
        aliases=tuple(normalize_entity_aliases(entity.aliases if entity else None)),
        area_id=resolve_area_id(hass, entity_id),
    )


def get_entity_prompt_metadata(
    hass: HomeAssistant, entity_id: str
) -> EntityPromptMetadata:
    """Return cached static metadata while preserving a no-cache test/setup fallback."""
    cache = hass.data.get(_CACHE_KEY)
    if not isinstance(cache, dict):
        return _build_metadata(hass, entity_id)
    metadata = cache.get(entity_id)
    if isinstance(metadata, EntityPromptMetadata):
        return metadata
    metadata = _build_metadata(hass, entity_id)
    cache[entity_id] = metadata
    return metadata


async def async_setup_entity_context_cache(hass: HomeAssistant) -> None:
    """Install registry invalidation for static entity prompt metadata."""
    if _CACHE_KEY in hass.data:
        return
    cache: dict[str, EntityPromptMetadata] = {}
    hass.data[_CACHE_KEY] = cache

    @callback
    def clear_cache(_event: Event) -> None:
        cache.clear()

    hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, clear_cache)
    hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, clear_cache)
