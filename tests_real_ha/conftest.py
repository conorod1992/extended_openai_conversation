"""Fixtures for acceptance tests against a genuine Home Assistant instance."""

from pathlib import Path
import sys

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import recorder as recorder_helper
from homeassistant.setup import async_setup_component

# Keep these tests independent from tests/conftest.py, whose ``hass`` fixture is a
# deliberate MagicMock used by the fast unit/regression suite.  Adding the repository
# root here also makes ``pytest tests_real_ha/`` work outside GitHub Actions.
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


@pytest.fixture(autouse=True)
async def real_ha_prerequisites(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """Initialize HA services that bootstrap normally provides before integrations."""
    # Core tests explicitly set up the Home Assistant integration before tests that
    # rely on exposed-entity preferences.  The lightweight test ``hass`` fixture does
    # not run the full bootstrap sequence itself.
    assert await async_setup_component(hass, "homeassistant", {})

    # Recorder is a hard dependency of this integration.  Normal HA bootstrap creates
    # its shared RecorderData before component setup; the standalone test fixture does
    # not.  Initialize only that bootstrap-owned data and let Home Assistant perform
    # the actual recorder component setup when resolving the integration dependency.
    if recorder_helper.DATA_RECORDER not in hass.data:
        recorder_helper.async_initialize_recorder(hass)

    yield
