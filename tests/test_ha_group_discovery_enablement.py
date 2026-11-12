"""Group switches gate discovery without depending on advertised schemas."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    async_discover,
    current_snapshot,
    new_reference_tool,
)
from tests.test_function_groups import _group
from tests.test_ha_llm_tools import Echo, TestAPI, context, register


@pytest.mark.parametrize(
    "active", ["none", "ungrouped", "always", "on_demand", "disabled_member"]
)
async def test_only_enabled_references_discover_shared_source_and_reenable(
    hass, active
):
    other = Echo()
    other.name = "other"
    api = register(hass, TestAPI(hass, tools=[Echo(), other]))
    discovered = await async_discover(hass, context())
    saved = [
        new_reference_tool(item.reference, set()) for item in discovered.tools.values()
    ]
    held, shared = saved
    held["spec"]["name"] = "held_echo"
    shared["spec"]["name"] = "shared_other"
    held["enabled"] = True
    shared["enabled"] = active != "disabled_member"
    groups = [_group("held", ["held_echo"], "always", enabled=False)]
    tools = [held]
    if active != "none":
        tools.append(shared)
        if active != "ungrouped":
            groups.append(
                _group(
                    "active",
                    ["shared_other"],
                    "on_demand" if active == "on_demand" else "always",
                )
            )
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.subentry = SimpleNamespace(data={"function_groups": groups})
    entity._configured_function_tools_from_data = MagicMock(return_value=tools)
    entity._effective_guest_policy = lambda: SimpleNamespace(guest_active=False)
    snapshots = []

    async def handle(*_args):
        snapshots.append(current_snapshot())
        return "done"

    entity._async_handle_message = AsyncMock(side_effect=handle)
    user = SimpleNamespace(as_llm_context=lambda _: context())
    before = deepcopy(tools)
    api.contexts.clear()
    await entity._async_handle_message_with_ha_tools(user, object())
    assert len(api.contexts) == int(active not in {"none", "disabled_member"})
    assert len(snapshots[-1].tools) == int(active not in {"none", "disabled_member"})
    # No group session was loaded, including the enabled on-demand group.
    assert tools == before
    groups[0]["enabled"] = True
    await entity._async_handle_message_with_ha_tools(user, object())
    assert len(api.contexts) == 1 + int(active not in {"none", "disabled_member"})
    assert len(snapshots[-1].tools) == 1 + int(
        active not in {"none", "disabled_member"}
    )
    assert tools == before
