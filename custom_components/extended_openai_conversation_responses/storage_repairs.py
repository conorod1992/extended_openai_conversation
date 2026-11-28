"""Publish Repairs for confirmed retained-data and restore barriers."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_STORES = {
    "archive": "Conversation Archive",
    "knowledge": "Knowledge Library",
    "memory": "Persistent Memory",
    "restore": "Backup & Restore",
}


def set_storage_repair(
    hass: HomeAssistant, entry_id: str, subentry_id: str, store: str, required: bool
) -> None:
    """Reflect a confirmed result for an existing assistant."""
    get_entry = getattr(getattr(hass, "config_entries", None), "async_get_entry", None)
    if not callable(get_entry):
        return
    entry = get_entry(entry_id)
    if (
        not isinstance(entry, ConfigEntry)
        or (subentry := entry.subentries.get(subentry_id)) is None
    ):
        return
    issue_id = f"storage_repair_{store}_{entry_id}_{subentry_id}"
    exists = ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    if exists == required:
        return
    if required:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="assistant_storage_repair",
            translation_placeholders={
                "assistant": subentry.title,
                "store": _STORES[store],
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def remove_storage_repairs(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> None:
    """Remove all repair entries owned by a deleted assistant."""
    for store in _STORES:
        ir.async_delete_issue(
            hass, DOMAIN, f"storage_repair_{store}_{entry_id}_{subentry_id}"
        )
