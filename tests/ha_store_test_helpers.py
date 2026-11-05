"""Select native HA disk/serialization boundaries without replacing Store behavior."""


def disk_write_boundary(store):
    if hasattr(store, "_io") or store._serialize_in_event_loop:
        return prepared_disk_write_boundary(store)
    return store, "_write_data"


def prepared_disk_write_boundary(store):
    """Select this Store's native writer after serialization in either HA layout.

    Both legacy serialization paths delegate to _write_prepared_data. Newer HA
    moves the same (mode, JSON) boundary into the instance's I/O backend. Unknown
    layouts fail explicitly: fault tests must never silently miss their writer.
    """
    owner, name = (
        (store._io, "_write")
        if hasattr(store, "_io")
        else (store, "_write_prepared_data")
    )
    if not callable(getattr(owner, name, None)):
        raise AssertionError(
            "Unsupported HA prepared disk writer; update the storage fault adapter"
        )
    return owner, name


def serialization_boundary(store):
    return store, "_write_data" if hasattr(store, "_write_data") else "_prepare_data"


def invalidate_store_cache(store):
    """Force a physical read without assuming Store owns the native cache."""
    from homeassistant.helpers.storage import get_internal_store_manager

    get_internal_store_manager(store.hass).async_invalidate(store.key)


async def initialize_config_entries(hass):
    """Real registry loads require the real config-entry storage barrier on new HA."""
    if hass.config_entries is None:
        from homeassistant.config_entries import ConfigEntries

        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
