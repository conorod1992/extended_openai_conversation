"""Genuine Home Assistant acceptance for HA-owned LLM Function Tools."""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    async_discover,
    preview_context,
    reference_key,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from tests_real_ha.test_management_backend_acceptance import (
    ADMIN_ID,
    _admin_client,
    _entry,
    _fresh_reload,
    _management_call,
    _setup_entry,
)


class AcceptanceEchoTool(llm.Tool):
    """Synthetic capability registered through Home Assistant's real LLM registry."""

    name = "acceptance_echo"
    description = "Echo a value through the genuine Home Assistant LLM tool API."
    parameters = vol.Schema(
        {
            vol.Required("value"): str,
            vol.Optional("repeat", default=1): vol.All(int, vol.Range(min=1, max=3)),
        }
    )

    def __init__(self) -> None:
        self.calls: list[tuple[llm.ToolInput, llm.LLMContext]] = []

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        self.calls.append((tool_input, llm_context))
        repeat = tool_input.tool_args.get("repeat", 1)
        return {
            "echo": tool_input.tool_args["value"] * repeat,
            "user_id": llm_context.context.user_id,
        }


class AcceptanceAPI(llm.API):
    """Deterministic third-party-style LLM API owned by Home Assistant."""

    def __init__(self, hass: HomeAssistant, tool: AcceptanceEchoTool) -> None:
        super().__init__(
            hass=hass,
            id="extended_openai_acceptance_api",
            name="Extended OpenAI acceptance API",
        )
        self.tool = tool
        self.contexts: list[llm.LLMContext] = []

    async def async_get_api_instance(
        self, context: llm.LLMContext
    ) -> llm.APIInstance:
        self.contexts.append(context)
        return llm.APIInstance(
            api=self,
            api_prompt="Use the acceptance tool when explicitly requested.",
            llm_context=context,
            tools=[self.tool],
        )


def _llm_context() -> llm.LLMContext:
    return llm.LLMContext(
        platform=DOMAIN,
        context=Context(user_id=ADMIN_ID),
        language="en",
        assistant="conversation",
        device_id=None,
    )


@pytest.mark.asyncio
async def test_real_ha_llm_tool_catalog_add_reload_and_dispatch(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A real HA LLM tool is discovered, saved, reloaded and dispatched."""
    entry = _entry("HA LLM Tool Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    tool = AcceptanceEchoTool()
    api = AcceptanceAPI(hass, tool)
    llm.async_register_api(hass, api)

    catalog = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="ha_catalog",
    )
    discovered = next(
        item for item in catalog["tools"] if item["name"] == tool.name
    )
    reference = discovered["reference"]
    assert reference == {
        "type": "ha_llm",
        "source_type": "api",
        "source_id": f"{type(tool).__module__}.{type(tool).__qualname__}",
        "api_id": api.id,
        "tool_name": tool.name,
    }
    assert discovered["description"] == tool.description
    assert discovered["already_added"] is False

    added = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="ha_add",
        tools=[reference],
    )
    saved = next(
        item
        for item in added["functions"]
        if item.get("function") == reference
    )
    local_name = saved["spec"]["name"]
    assert local_name.startswith("ha_")
    # HA owns the live description/schema. Only stable identity is persisted.
    assert saved["spec"] == {"name": local_name}

    await hass.async_block_till_done()
    await _fresh_reload(hass, entry)

    reloaded = await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="get",
    )
    persisted = next(
        item
        for item in reloaded["config"]["functions"]
        if item.get("function") == reference
    )
    assert persisted == saved

    refreshed_catalog = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="ha_catalog",
    )
    refreshed = next(
        item for item in refreshed_catalog["tools"] if item["reference"] == reference
    )
    assert refreshed["already_added"] is True
    assert refreshed_catalog["saved"][local_name]["available"] is True

    snapshot = await async_discover(hass, _llm_context(), [reference])
    live = snapshot.tools[reference_key(reference)]
    projected = snapshot.project([persisted])[0]
    assert projected["ha_available"] is True
    assert projected["spec"]["name"] == local_name
    properties = projected["spec"]["parameters"]["properties"]
    assert "value" in properties
    assert "repeat" in properties

    result = await live.async_call(
        llm.ToolInput(
            tool_name=local_name,
            tool_args={"value": "ha", "repeat": 2},
            id="acceptance-call",
        )
    )
    assert result == {"echo": "haha", "user_id": ADMIN_ID}
    assert len(tool.calls) == 1
    assert tool.calls[0][0].tool_name == tool.name
    assert tool.calls[0][0].external is False
    assert tool.calls[0][1].context.user_id == ADMIN_ID


@pytest.mark.parametrize("arguments", [{}, {"value": 42}])
async def test_public_assist_invalid_native_arguments_settle_and_recover(hass, monkeypatch, arguments):
    import json
    from custom_components.extended_openai_conversation_responses.const import CONF_FUNCTION_TOOLS
    from custom_components.extended_openai_conversation_responses.ha_llm_tools import new_reference_tool
    from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech

    tool = AcceptanceEchoTool()
    api = AcceptanceAPI(hass, tool)
    llm.async_register_api(hass, api)
    reference = {
        "type": "ha_llm", "source_type": "api", "api_id": api.id,
        "source_id": f"{type(tool).__module__}.{type(tool).__qualname__}",
        "tool_name": tool.name,
    }
    saved = new_reference_tool(reference, set())
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [saved]})
    sent = _provider(monkeypatch, agent, [
        {"index": 0, "id": "invalid-native", "type": "function", "function": {
            "name": saved["spec"]["name"], "arguments": json.dumps(arguments)}},
        "Please supply a valid value.",
        {"index": 0, "id": "valid-native", "type": "function", "function": {
            "name": saved["spec"]["name"], "arguments": '{"value":"recovered"}'}},
        "Recovered.",
    ])
    first = await _say(hass, agent, "Call the native tool")
    assert _speech(first) == "Please supply a valid value."
    assert not tool.calls
    results = [item for item in sent[1]["messages"] if item["role"] == "tool"]
    assert [item["tool_call_id"] for item in results] == ["invalid-native"]
    assert "arguments do not match" in results[0]["content"]
    second = await _say(hass, agent, "Try a valid value", first.conversation_id)
    assert _speech(second) == "Recovered."
    assert len(tool.calls) == 1
    assert tool.calls[0][0].tool_args["value"] == "recovered"
    assert len([item for item in sent[3]["messages"] if item["role"] == "tool" and item["tool_call_id"] == "invalid-native"]) == 1


async def test_public_assist_stops_after_ha_tool_loses_acknowledgement(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
        new_reference_tool,
    )
    from tests_real_ha.test_cross_feature_acceptance import (
        _agent,
        _provider,
        _say,
        _speech,
    )

    class ActionTool(AcceptanceEchoTool):
        integration = DOMAIN

        async def async_call(self, hass, tool_input, llm_context):
            self.calls.append((tool_input, llm_context))
            raise HomeAssistantError("Private acknowledgement failure")

    tool = ActionTool()
    api = AcceptanceAPI(hass, tool)
    llm.async_register_api(hass, api)
    saved = new_reference_tool(
        {
            "type": "ha_llm",
            "source_type": "api",
            "api_id": api.id,
            "source_id": f"{type(tool).__module__}.{type(tool).__qualname__}",
            "tool_name": tool.name,
        },
        set(),
    )
    agent = await _agent(hass, functions=[saved], function_tool_error_recovery=False)
    sent = _provider(
        monkeypatch,
        agent,
        [
            {
                "index": 0,
                "id": "lost-ack",
                "type": "function",
                "function": {
                    "name": saved["spec"]["name"],
                    "arguments": '{"value":"run"}',
                },
            },
        ],
    )
    result = await _say(hass, agent, "Call the test action")
    assert "may have taken effect" in _speech(result, successful=False)
    assert len(sent) == len(tool.calls) == 1


@pytest.mark.parametrize("prefer_eoai", [False, True])
async def test_real_ha_assist_timer_schema_and_dispatch_preserve_ordinary_timers(
    hass, prefer_eoai
):
    """Use the supported HA version's actual Assist tool and timer manager."""
    from unittest.mock import Mock

    from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
        new_reference_tool,
    )
    from custom_components.extended_openai_conversation_responses.local_intents import (
        CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI,
    )
    from homeassistant.components.intent import timers
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "intent", {})
    event = Mock()
    device_id = "test-timer-device"
    unregister = timers.async_register_timer_handler(hass, device_id, event)
    manager = hass.data[timers.TIMER_DATA]
    try:
        source = await async_discover(
            hass, preview_context(hass, None, device_id=device_id)
        )
        key, native = next(
            (key, live)
            for key, live in source.tools.items()
            if live.reference["api_id"] == llm.LLM_API_ASSIST
            and live.reference["tool_name"] in {"HassStartTimer", "intent__HassStartTimer"}
        )
        saved = new_reference_tool(native.reference, set())
        assert saved["spec"]["name"] != native.tool.name
        native_properties = set(native.spec["parameters"]["properties"])
        native_parameters = native.tool.parameters
        snapshot = source.with_delayed_action_preference(
            {CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI: prefer_eoai},
        )
        properties = set(
            snapshot.project([saved])[0]["spec"]["parameters"]["properties"]
        )
        assert properties == native_properties - (
            {"conversation_command"} if prefer_eoai else set()
        )
        assert set(native.spec["parameters"]["properties"]) == native_properties
        assert native.tool.parameters is native_parameters

        live = snapshot.tools[key]

        def timer_call(**extra):
            return llm.ToolInput(
                id="timer",
                tool_name=saved["spec"]["name"],
                tool_args={"minutes": 1, **extra},
            )

        if prefer_eoai:
            with pytest.raises(HomeAssistantError, match="ordinary timers"):
                await live.async_call(timer_call(conversation_command="turn the light off"))
            assert not manager.timers
        elif "conversation_command" in native_properties:
            await live.async_call(timer_call(conversation_command="turn the light off"))

        if "conversation_command" not in native_properties:
            schedule_key, schedule_tool = next(
                (key, tool)
                for key, tool in source.tools.items()
                if tool.is_native_delayed_command
            )
            saved_schedule = new_reference_tool(schedule_tool.reference, set())
            command_call = llm.ToolInput(
                id="schedule",
                tool_name=saved_schedule["spec"]["name"],
                tool_args={"minutes": 1, "command": "turn the light off"},
            )
            assert command_call.tool_name != schedule_tool.tool.name
            assert (
                snapshot.project([saved_schedule])[0]["ha_available"] is not prefer_eoai
            )
            if prefer_eoai:
                assert schedule_key not in snapshot.tools
                with pytest.raises(
                    HomeAssistantError, match="Use an EOAI Function Tool"
                ):
                    await schedule_tool.restrict_delayed_commands().async_call(
                        command_call
                    )
            else:
                await snapshot.tools[schedule_key].async_call(command_call)

        assert [timer.conversation_command for timer in manager.timers.values()] == (
            [] if prefer_eoai else ["turn the light off"]
        )
        with pytest.raises(HomeAssistantError, match="requested schema"):
            await live.async_call(timer_call(minutes=-1))
        event.assert_not_called()
        await live.async_call(timer_call())
        event.assert_called_once()
        assert event.call_args.args[0] == timers.TimerEventType.STARTED
    finally:
        for timer_id in tuple(manager.timers):
            manager.cancel_timer(timer_id)
        unregister()
