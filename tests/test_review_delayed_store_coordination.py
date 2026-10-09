"""Shared delayed-call storage must not revive another agent's deleted data."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from custom_components.extended_openai_conversation_responses import (
    delayed_tools as delayed,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests.test_delayed_tools import _record


class SharedStorage:
    def __init__(self, data):
        self.data = deepcopy(data)

    async def async_load(self):
        snapshot = deepcopy(self.data)
        await asyncio.sleep(0)
        return snapshot

    async def async_save(self, data):
        await asyncio.sleep(0)
        self.data = deepcopy(data)


@pytest.mark.parametrize("other_entry", ["entry", "other-entry"])
async def test_concurrent_unloaded_deletions_preserve_only_other_agents(
    hass, monkeypatch, other_entry
):
    keep = {
        "entry_id": "entry",
        "subentry_id": "keep",
        "arguments": {"keep": "private"},
    }
    store = SharedStorage(
        {
            "calls": [
                {
                    "entry_id": "entry",
                    "subentry_id": "a",
                    "arguments": {"a": "private"},
                },
                {
                    "entry_id": other_entry,
                    "subentry_id": "b",
                    "arguments": {"b": "private"},
                },
                keep,
            ],
            "metadata": "preserved",
        }
    )
    monkeypatch.setattr(
        delayed, "PropagatingWriteStore", lambda *_args, **_kwargs: store
    )
    await asyncio.gather(
        delayed.async_remove_stored_agent_calls(hass, "entry", "a"),
        delayed.async_remove_stored_agent_calls(hass, other_entry, "b"),
    )
    assert store.data == {"calls": [keep], "metadata": "preserved"}
    assert delayed.DATA_DELAYED_TOOL_MANAGER not in hass.data.get(DOMAIN, {})


@pytest.mark.parametrize("setup_first", [False, True])
async def test_deletion_and_manager_startup_share_storage_generation(
    hass, monkeypatch, setup_first
):
    first = _record()
    keep = replace(first, call_id="keep", subentry_id="keep")
    store = SharedStorage({"calls": [first.as_dict(), keep.as_dict()]})
    monkeypatch.setattr(
        delayed, "PropagatingWriteStore", lambda *_args, **_kwargs: store
    )
    manager = delayed.DelayedToolManager(hass)
    hass.data.setdefault(DOMAIN, {})[delayed.DATA_DELAYED_TOOL_MANAGER] = manager
    setup = manager.async_setup()
    deletion = delayed.async_remove_stored_agent_calls(hass, "entry", "agent")
    await asyncio.gather(*([setup, deletion] if setup_first else [deletion, setup]))
    assert set(manager._records) == {"keep"}
    assert [call["call_id"] for call in store.data["calls"]] == ["keep"]
    await manager.async_remove_agent("entry", "keep")
    assert store.data["calls"] == []


async def test_cancelled_fallback_save_settles_before_next_deletion(hass, monkeypatch):
    store = SharedStorage(
        {
            "calls": [
                {"entry_id": "entry", "subentry_id": "a"},
                {"entry_id": "entry", "subentry_id": "b"},
            ]
        }
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    original_save = store.async_save

    async def save(data):
        entered.set()
        await release.wait()
        await original_save(data)

    store.async_save = save
    monkeypatch.setattr(
        delayed, "PropagatingWriteStore", lambda *_args, **_kwargs: store
    )
    first = asyncio.create_task(
        delayed.async_remove_stored_agent_calls(hass, "entry", "a")
    )
    await entered.wait()
    first.cancel()
    second = asyncio.create_task(
        delayed.async_remove_stored_agent_calls(hass, "entry", "b")
    )
    await asyncio.sleep(0)
    assert not first.done() and not second.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert store.data["calls"] == []
