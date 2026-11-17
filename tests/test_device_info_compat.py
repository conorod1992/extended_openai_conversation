"""Native DeviceInfo reads retain values without using deprecated mapping APIs."""

from dataclasses import dataclass

import pytest

from tests_real_ha.device_info_compat import device_info_field


@dataclass
class NativeInfo:
    name: str
    model: str | None
    identifiers: set

    def __getitem__(self, field):
        raise AssertionError("Deprecated native DeviceInfo indexing")


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("field", ["name", "model", "identifiers"])
def test_device_info_fields_preserve_native_and_legacy_values(native, field):
    values = {"name": "Renamed", "model": None, "identifiers": {("eoai", "entry")}}
    info = NativeInfo(**values) if native else values
    assert device_info_field(info, field) == values[field]
