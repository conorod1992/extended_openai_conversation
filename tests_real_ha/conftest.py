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


from custom_components.extended_openai_conversation_responses.restore_recovery import (  # noqa: E402
    _RestoreJournalStore,
)


@pytest.fixture(autouse=True)
async def real_ha_prerequisites(
    hass: HomeAssistant,
    enable_custom_integrations,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Initialize HA services that bootstrap normally provides before integrations."""
    # The pytest-HA shim injects serialized Store envelopes through _data rather
    # than files. Extend that shim to the strict journal reader for ordinary HA
    # tests; OS/process-fresh probes deliberately retain the actual reader.
    if "real_store_io" not in request.fixturenames:
        from homeassistant.helpers.storage import Store

        monkeypatch.setattr(
            _RestoreJournalStore, "_async_load_data", Store._async_load_data
        )

    # The upstream fixture uses one package-owned testing_config directory.
    # Genuine startup creates TTS cache and Recorder files there; xdist workers
    # can race mkdir/schema creation. Give each HA instance its own filesystem.
    hass.config.config_dir = str(tmp_path)

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
