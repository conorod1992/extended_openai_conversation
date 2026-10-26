"""The current diagnostic harness can load on a historical test checkout."""

from pathlib import Path
import runpy
import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize("recorder_exists", [False, True])
async def test_historical_fixture_module_is_not_a_bootstrap_dependency(
    monkeypatch, tmp_path, recorder_exists
):
    # A historical conftest has neither today's hass_config_dir nor the current
    # restore reader class. Loading and exercising the overlay must still work.
    monkeypatch.setitem(
        sys.modules, "tests_real_ha.conftest", ModuleType("historical_conftest")
    )
    path = Path(__file__).resolve().parents[1] / "ci/frontend_latency/conftest.py"
    fixtures = runpy.run_path(str(path))
    recorder = fixtures["recorder_helper"]
    hass = Mock(data={recorder.DATA_RECORDER: object()} if recorder_exists else {})
    setup = AsyncMock(return_value=True)
    initialize = Mock()
    fixtures["real_ha_prerequisites"].__wrapped__.__globals__[
        "async_setup_component"
    ] = setup
    monkeypatch.setattr(recorder, "async_initialize_recorder", initialize)
    assert fixtures["hass_config_dir"].__wrapped__(tmp_path) == str(tmp_path)
    await fixtures["real_ha_prerequisites"].__wrapped__(hass, None)
    setup.assert_awaited_once_with(hass, "homeassistant", {})
    assert initialize.call_args_list == ([] if recorder_exists else [((hass,), {})])
