"""Deterministic guards for the released upgrade process boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests_real_ha import test_release_upgrade_acceptance as upgrade


@pytest.mark.parametrize("historical", [True, False])
def test_release_model_customization_survives_candidate_save_and_request(historical):
    from custom_components.extended_openai_conversation_responses import const, helpers
    from custom_components.extended_openai_conversation_responses.agent_config import (
        merge_agent_config,
        normalize_agent_config,
    )
    from custom_components.extended_openai_conversation_responses.request import (
        build_provider_request_snapshot,
    )

    # Releases through 6.7.0 defaulted to flex. Keep that historical starting
    # point while applying precisely the settings used by the real HA journey.
    data = {"service_tier": "flex", "functions": [], "api_mode": "chat_completions"}
    settings = upgrade._released_model_settings(
        const, SimpleNamespace() if historical else helpers
    )
    data.update({getattr(const, name): value for name, value in settings})
    normalized = normalize_agent_config(data)
    saved = merge_agent_config(normalized, {"max_tokens": 640})
    assert saved["service_tier"] == "default"
    request = build_provider_request_snapshot(saved, {}, tools_required=True)
    assert request.api_mode == "chat_completions"


async def test_upgrade_browser_bridge_supports_catalogue_status():
    from custom_components.extended_openai_conversation_responses.model_catalog_manager import (
        DATA_MANAGER,
    )
    from tests_real_ha.test_browser_release_upgrade_acceptance import (
        _model_catalog_result,
    )

    status = {"catalog_version": 1, "generation": "current"}
    manager = SimpleNamespace(status=Mock(return_value=status))
    hass = SimpleNamespace(data={DATA_MANAGER: manager})
    assert await _model_catalog_result(hass, {"action": "status"}) is status
    manager.status.assert_called_once_with()


async def test_checkpoint_reports_early_conversation_failure():
    async def fail():
        raise TypeError("released HA tool API changed")

    request = asyncio.create_task(fail())
    with pytest.raises(TypeError, match="released HA tool API changed"):
        await upgrade._wait_for_active_checkpoint(asyncio.Event(), request)


async def test_checkpoint_rejects_early_completed_conversation():
    async def finish():
        return "controlled conversation error"

    with pytest.raises(AssertionError, match="completed before checkpoint"):
        await upgrade._wait_for_active_checkpoint(
            asyncio.Event(), asyncio.create_task(finish())
        )


async def test_checkpoint_keeps_pending_request_active():
    blocked = asyncio.Event()
    blocked.set()
    request = asyncio.create_task(asyncio.Event().wait())
    try:
        await upgrade._wait_for_active_checkpoint(blocked, request)
        assert not request.done()
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize(
    "phase", ["released", "candidate-migrate", "candidate-restart"]
)
def test_child_selects_historical_runtime_only_for_released_phase(
    monkeypatch, tmp_path, phase
):
    module = upgrade
    monkeypatch.setenv("UPGRADE_RELEASED_PYTHON", "/reviewed/released/python")
    monkeypatch.setattr(module, "_ensure_process_config", Mock())
    run = Mock()
    monkeypatch.setattr(module.subprocess, "run", run)
    module._run_child(tmp_path, phase)
    expected = (
        "/reviewed/released/python" if phase == "released" else module.sys.executable
    )
    assert run.call_args.args[0][0] == expected
