"""Sensitivity controls distinguish inert privacy fences from leaked resources."""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    AgentMaintenanceGate,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests.resource_lifecycle_ledger import assert_owned_removed, capture_owned


@pytest.mark.parametrize(
    "state", ["deleted", "live", "writer", "reader", "accepts_work"]
)
def test_retirement_fence_must_be_deleted_idle_and_deny_work(tmp_path, state):
    gate = AgentMaintenanceGate()
    gate.deleted = state != "live"
    if state == "writer":
        gate._writer_active = True
    if state == "reader":
        gate._active_readers = 1
    if state == "accepts_work":
        gate.require_available = lambda: None
    hass = SimpleNamespace(
        data={f"{DOMAIN}.agent_maintenance_gates": {("entry", "agent"): gate}},
        config=SimpleNamespace(path=lambda _: str(tmp_path)),
    )
    snapshot = capture_owned(hass, "entry", "agent")
    if state == "deleted":
        assert_owned_removed(snapshot, snapshot)
    else:
        with pytest.raises((AssertionError, pytest.fail.Exception)):
            assert_owned_removed(snapshot, snapshot)


@pytest.mark.parametrize("retired", [False, True])
def test_callback_retirement_survives_registry_and_attribute_removal(tmp_path, retired):
    calls = []
    manager = SimpleNamespace(_cancel_retention=lambda: calls.append("unsubscribed"))
    registry = {("entry", "agent"): manager}
    hass = SimpleNamespace(
        data={f"{DOMAIN}.usage_managers": registry},
        config=SimpleNamespace(path=lambda _: str(tmp_path)),
    )
    before = capture_owned(hass, "entry", "agent")
    if retired:
        manager._cancel_retention()
    manager._cancel_retention = None
    registry.clear()
    after = capture_owned(hass, "entry", "agent")
    if retired:
        assert_owned_removed(before, after)
        assert calls == ["unsubscribed"]
    else:
        with pytest.raises(AssertionError, match="unsubscribe"):
            assert_owned_removed(before, after)


@pytest.mark.parametrize("retired", [False, True])
async def test_exact_task_must_settle_after_registry_removal(tmp_path, retired):
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    sibling = asyncio.create_task(release.wait())
    registry = {
        ("entry", "agent"): SimpleNamespace(_prune_task=task),
        ("entry", "sibling"): SimpleNamespace(_prune_task=sibling),
    }
    hass = SimpleNamespace(
        data={f"{DOMAIN}.usage_managers": registry},
        config=SimpleNamespace(path=lambda _: str(tmp_path)),
    )
    try:
        before = capture_owned(hass, "entry", "agent")
        registry.pop(("entry", "agent"))
        if retired:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        after = capture_owned(hass, "entry", "agent")
        if retired:
            assert_owned_removed(before, after)
        else:
            with pytest.raises(AssertionError, match="task survived"):
                assert_owned_removed(before, after)
        assert not sibling.done()
    finally:
        release.set()
        await asyncio.gather(task, sibling, return_exceptions=True)
