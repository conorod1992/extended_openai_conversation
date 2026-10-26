"""Bootstrap native HA independently of the measured checkout's test fixtures.

This harness is overlaid on historical revisions. Their conftest modules need
not contain today's fixtures (or today's production restore internals).
"""

from pathlib import Path
import sys

import pytest

from homeassistant.helpers import recorder as recorder_helper
from homeassistant.setup import async_setup_component

# Standalone pytest entry points need the overlay's repository on sys.path too;
# the historical tests_real_ha conftest previously supplied this incidentally.
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


@pytest.fixture
def hass_config_dir(tmp_path: Path) -> str:
    """Bind native Store paths before HA constructs its shared stores."""
    return str(tmp_path)


@pytest.fixture(autouse=True)
async def real_ha_prerequisites(hass, enable_custom_integrations):
    """Provide the bootstrap state needed by both measured implementations."""
    assert await async_setup_component(hass, "homeassistant", {})
    if recorder_helper.DATA_RECORDER not in hass.data:
        recorder_helper.async_initialize_recorder(hass)
