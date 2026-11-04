"""Select native HA disk/serialization boundaries without replacing Store behavior."""


def disk_write_boundary(store):
    if hasattr(store, "_io"):
        return store._io, "_write"
    return store, "_write_prepared_data" if store._serialize_in_event_loop else "_write_data"


def serialization_boundary(store):
    return store, "_write_data" if hasattr(store, "_write_data") else "_prepare_data"


async def initialize_config_entries(hass):
    """Real registry loads require the real config-entry storage barrier on new HA."""
    if hass.config_entries is None:
        from homeassistant.config_entries import ConfigEntries
        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
