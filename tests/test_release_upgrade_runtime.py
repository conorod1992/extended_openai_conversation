"""Deterministic guards for the released upgrade process boundary."""

import asyncio
from unittest.mock import Mock

import pytest

from tests_real_ha import test_release_upgrade_acceptance as upgrade


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
