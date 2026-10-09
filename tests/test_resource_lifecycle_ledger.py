"""Sensitivity controls distinguish inert privacy fences from leaked resources."""
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_maintenance import AgentMaintenanceGate
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests.resource_lifecycle_ledger import assert_owned_removed, capture_owned


@pytest.mark.parametrize("state", ["deleted", "live", "writer", "reader", "accepts_work"])
def test_retirement_fence_must_be_deleted_idle_and_deny_work(tmp_path, state):
    gate = AgentMaintenanceGate()
    gate.deleted = state != "live"
    if state == "writer":
        gate._writer_active = True
    if state == "reader":
        gate._active_readers = 1
    if state == "accepts_work":
        gate.require_available = lambda: None
    hass = SimpleNamespace(data={f"{DOMAIN}.agent_maintenance_gates": {("entry", "agent"): gate}},
                           config=SimpleNamespace(path=lambda _: str(tmp_path)))
    snapshot = capture_owned(hass, "entry", "agent")
    if state == "deleted":
        assert_owned_removed(snapshot, snapshot)
    else:
        with pytest.raises((AssertionError, pytest.fail.Exception)):
            assert_owned_removed(snapshot, snapshot)
