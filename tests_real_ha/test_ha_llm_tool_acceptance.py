"""Genuine Home Assistant acceptance for HA-owned LLM Function Tools."""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    async_discover,
    reference_key,
)
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
