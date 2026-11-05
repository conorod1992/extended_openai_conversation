"""Fault injection must reject unknown native writer layouts explicitly."""

from types import SimpleNamespace

import pytest

from tests.ha_store_test_helpers import prepared_disk_write_boundary


@pytest.mark.parametrize(
    "store",
    [
        object(),
        SimpleNamespace(_io=SimpleNamespace(), _write_prepared_data=lambda *_: None),
        SimpleNamespace(_io=SimpleNamespace(_write=None)),
    ],
)
def test_unrecognized_prepared_writer_cannot_silently_disable_a_fault(store):
    with pytest.raises(AssertionError, match="Unsupported HA prepared disk writer"):
        prepared_disk_write_boundary(store)
