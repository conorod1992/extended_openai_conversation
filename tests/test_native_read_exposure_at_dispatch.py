"""Native reads must recheck exposure at every Composite dispatch boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    EntityNotExposed,
)
from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.native_read_scope import (
    native_read_scope,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("revoked", [False, True])
async def test_history_exposure_revalidated_for_direct_and_nested_reads(
    tmp_path, monkeypatch, depth, revoked
):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.private", "42")
    read = AsyncMock(return_value=[[{"entity_id": "sensor.private", "state": "42"}]])
    monkeypatch.setattr(
        native.recorder,
        "get_instance",
        lambda _: SimpleNamespace(async_add_executor_job=read),
    )
    agent = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = hass
    agent.entry = SimpleNamespace(entry_id="entry")
    agent.subentry = SimpleNamespace(subentry_id="agent", data={"function_groups": []})
    agent.entity_id = "conversation.audit"
    advertised = [{"entity_id": "sensor.private"}]
    agent._get_exposed_entities = Mock(return_value=[] if revoked else advertised)
    agent._effective_guest_policy = Mock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    config = {"type": "native", "name": "get_history"}
    for _ in range(depth):
        config = {"type": "composite", "sequence": [config]}
    tool = {
        "spec": {
            "name": "read_history",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_ids": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["entity_ids"],
            },
        },
        "function": config,
    }
    agent._configured_function_tools_from_data = Mock(return_value=[tool])
    result = await agent._execute_function_tool(
        tool,
        llm.ToolInput(
            id="read",
            tool_name="read_history",
            tool_args={"entity_ids": ["sensor.private"]},
        ),
        None,
        advertised,
    )
    assert read.await_count == (0 if revoked else 1)
    # ToolResultContent differs between supported Core versions; the I/O count
    # is the disclosure boundary, independent of its final result envelope.
    assert result is not None


async def test_composite_rechecks_after_an_earlier_child_awaits(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.private", "42")
    entered, resume = asyncio.Event(), asyncio.Event()
    visible = [{"entity_id": "sensor.private"}]

    async def user_lookup(_):
        entered.set()
        await resume.wait()
        return SimpleNamespace(name="Reader")

    hass.auth = SimpleNamespace(async_get_user=user_lookup)
    read = AsyncMock(return_value="private history")
    monkeypatch.setattr(
        native.recorder,
        "get_instance",
        lambda _: SimpleNamespace(async_add_executor_job=read),
    )
    agent = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    agent.hass, agent.entity_id = hass, "conversation.audit"
    agent.entry = SimpleNamespace(entry_id="entry")
    agent.subentry = SimpleNamespace(subentry_id="agent", data={"function_groups": []})
    agent._get_exposed_entities = lambda: list(visible)
    agent._effective_guest_policy = Mock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    tool = {
        "spec": {
            "name": "read_history",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_ids": {"type": "array", "items": {"type": "string"}}
                },
            },
        },
        "function": {
            "type": "composite",
            "sequence": [
                {"type": "native", "name": "get_user_from_user_id"},
                {"type": "native", "name": "get_history"},
            ],
        },
    }
    agent._configured_function_tools_from_data = Mock(return_value=[tool])
    pending = asyncio.create_task(
        agent._execute_function_tool(
            tool,
            llm.ToolInput(
                id="read",
                tool_name="read_history",
                tool_args={"entity_ids": ["sensor.private"]},
            ),
            SimpleNamespace(context=Context(user_id="reader")),
            list(visible),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        visible.clear()
    finally:
        resume.set()
    await pending
    read.assert_not_awaited()


@pytest.mark.parametrize("name", ["get_history", "get_statistics", "get_energy"])
async def test_each_native_read_narrows_current_scope_without_widening_guest_admission(
    tmp_path, monkeypatch, name
):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.private", "42")
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(
        native.recorder,
        "get_instance",
        lambda _: SimpleNamespace(async_add_executor_job=read),
    )
    energy = SimpleNamespace(
        data={"energy_sources": [{"stat_energy_from": "sensor.private"}]}
    )
    monkeypatch.setattr(
        native.energy, "async_get_manager", AsyncMock(return_value=energy)
    )
    args = {
        "entity_ids": ["sensor.private"],
        "statistic_ids": ["sensor.private"],
        "start_time": "2026-11-01T00:00:00Z",
        "end_time": "2026-11-02T00:00:00Z",
    }
    function = native.NativeFunction()
    # A live resolver may see more entities than this Guest/caller admitted.
    with (
        native_read_scope(lambda: [{"entity_id": "sensor.private"}]),
        pytest.raises(EntityNotExposed),
    ):
        await getattr(function, name)(
            hass, {"type": "native", "name": name}, args, None, []
        )
    # Conversely, an originally admitted entity may no longer be exposed.
    with native_read_scope(lambda: []), pytest.raises(EntityNotExposed):
        await getattr(function, name)(
            hass,
            {"type": "native", "name": name},
            args,
            None,
            [{"entity_id": "sensor.private"}],
        )
    read.assert_not_awaited()


async def test_energy_revalidates_after_manager_load_yields(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    visible = [{"entity_id": "sensor.private"}]

    async def load_manager(_):
        await asyncio.sleep(0)
        visible.clear()
        return SimpleNamespace(
            data={"energy_sources": [{"stat_energy_from": "sensor.private"}]}
        )

    monkeypatch.setattr(native.energy, "async_get_manager", load_manager)
    with native_read_scope(lambda: list(visible)), pytest.raises(EntityNotExposed):
        await native.NativeFunction().get_energy(hass, {}, {}, None, list(visible))
