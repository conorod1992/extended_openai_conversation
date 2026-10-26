"""Apply the physical-store contract with stress-suite fixture discovery."""

from tests_real_ha.test_fixture_store_isolation import (  # noqa: F401
    test_core_config_store_and_fresh_reader_share_physical_path,
)
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401
