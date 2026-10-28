"""Persist physical Broadcast origin without inheriting live request context."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    delayed_tools as delayed,
)
from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.request_origin import (
    bind_request_satellite,
    get_request_satellite,
)
from homeassistant.core import Context, CoreState
from homeassistant.util import dt as dt_util
from tests.test_request_rules import MemoryStore


@pytest.mark.parametrize("legacy", [False, True])
async def test_delayed_broadcast_origin_survives_queue_reload(
    hass, monkeypatch, legacy
):
    tool = {
        "spec": {"name": "broadcast"},
        "function": {"type": "native", "name": "send_broadcast"},
    }
    subentry = SimpleNamespace(
        subentry_type="conversation", subentry_id="agent", data={}
    )
    entry = SimpleNamespace(
        entry_id="entry", disabled_by=None, subentries={"agent": subentry}
    )
    hass.config_entries.async_get_entry.return_value = entry
    hass.state = CoreState.not_running
    store = MemoryStore()
    original = delayed.DelayedToolManager(hass)
    original._store = store
    original._setup_complete = True
    with bind_request_satellite("assist_satellite.kitchen"):
        record = await original.async_schedule(
            SimpleNamespace(entry=entry, subentry=subentry),
            "broadcast",
            {"whole_home": True, "message": "Dinner"},
            SimpleNamespace(context=Context(), device_id=None),
            function_tool=tool,
            execution_delay={"seconds": 5},
        )
    raw = store.data["calls"][0]
    raw["due_at"] = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    if legacy:
        raw.pop("satellite_id", None)
    else:
        assert raw.get("satellite_id") == "assist_satellite.kitchen"
    manager = delayed.DelayedToolManager(hass)
    manager._store = store
    await manager.async_setup()
    monkeypatch.setattr(
        delayed, "configured_function_tools_from_data", lambda _: [tool]
    )
    monkeypatch.setattr(
        delayed, "latest_function_tool_for_execution", lambda _agent, current: current
    )
    from custom_components.extended_openai_conversation_responses.intercom import (
        IntercomManager,
    )

    intercom = IntercomManager(hass)
    intercom._enabled = True

    def resolve(**kw):
        candidates = kw.get("entity_ids") or [
            "assist_satellite.hall",
            "assist_satellite.kitchen",
        ]
        return [entity for entity in candidates if entity != kw.get("origin_entity_id")]

    monkeypatch.setattr(intercom, "resolve_targets", resolve)
    monkeypatch.setattr(intercom, "_schedule_drain", lambda _: None)
    monkeypatch.setattr(native, "async_get_intercom", AsyncMock(return_value=intercom))

    async def execute(_tool, call, context, exposed):
        await native.NativeFunction().send_broadcast(
            hass, {}, call.tool_args, context, exposed
        )

    monkeypatch.setattr(
        manager,
        "_resolve_agent",
        lambda *_: SimpleNamespace(_execute_function_tool=execute),
    )
    # An unrelated request must not supply the resumed job's origin.
    with bind_request_satellite("assist_satellite.unrelated"):
        assert not await manager._async_execute_due(record.call_id)
        assert get_request_satellite() == "assist_satellite.unrelated"
    sent = intercom.history()[0]
    assert sent["origin_entity_id"] == (None if legacy else "assist_satellite.kitchen")
    assert sent["targets"] == (
        ["assist_satellite.hall", "assist_satellite.kitchen"]
        if legacy
        else ["assist_satellite.hall"]
    )
    assert not manager._records


def test_invalid_persisted_satellite_is_rejected():
    from tests.test_delayed_tools import _record

    raw = deepcopy(_record().as_dict())
    raw["satellite_id"] = {"untrusted": "shape"}
    with pytest.raises(ValueError, match="satellite_id"):
        delayed.DelayedToolCall.from_dict(raw)
