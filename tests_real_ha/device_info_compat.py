"""Read native DeviceInfo fields across HA's dictionary/dataclass transition."""


def device_info_field(info, field):
    """Prefer the supported attribute API without invoking deprecated indexing."""
    if hasattr(info, field):
        return getattr(info, field)
    return info[field]
