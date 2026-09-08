"""Fixtures for acceptance tests against a genuine Home Assistant instance."""

from pathlib import Path
import sys

import pytest

# Keep these tests independent from tests/conftest.py, whose ``hass`` fixture is a
# deliberate MagicMock used by the fast unit/regression suite.  Adding the repository
# root here also makes ``pytest tests_real_ha/`` work outside GitHub Actions.
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant's real loader to discover this custom integration."""
    yield
