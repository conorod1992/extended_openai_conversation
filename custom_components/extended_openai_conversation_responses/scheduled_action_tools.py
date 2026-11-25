"""Integration-owned scheduled action tools for authenticated or physical origins."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from .delayed_tools import (
    _DELAYED_EXECUTION_MARKER,
    ScheduledActionOwner,
    delayed_tool_manager,
    valid_scheduled_action_id,
)
from .function_tool_policy import runtime_function_tool_name
from .model_payload import count_model_function_tools
from .request_origin import get_request_satellite

SCHEDULED_ACTION_TOOL_TYPE = "scheduled_actions"


def scheduled_action_tools() -> list[dict[str, Any]]:
    """Build fresh provider schemas without exposing ownership arguments."""
    return [
        {
            "spec": {
                "name": runtime_function_tool_name(
                    SCHEDULED_ACTION_TOOL_TYPE, operation
                ),
                "description": description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            },
            "function": {"type": SCHEDULED_ACTION_TOOL_TYPE, "operation": operation},
        }
        for operation, description, properties in (
            (
                "list",
                "List pending EOAI scheduled actions for this assistant and caller. "
                "Use the returned descriptions and times to resolve references to scheduled "
                "actions. Ask which action the user means if multiple actions could match.",
                {},
            ),
            (
                "cancel",
                "Cancel one pending EOAI scheduled action the user explicitly wants cancelled. "
                "Use its exact action ID from a result. If the action is unclear, list actions "
                "and ask the user to clarify instead of guessing. Confirm cancellation only if "
                "the tool returns status cancelled. For an uncertain result, say it could not "
                "be confirmed and check the list before attempting any further cancellation.",
                {
                    "action_id": {
                        "type": "string",
                        "description": "Exact ID returned by EOAI.",
                    }
                },
            ),
        )
    ]


def fit_scheduled_action_tools(
    tools: list[dict[str, Any]], limit: int | None, *, extra_tool_count: int
) -> list[dict[str, Any]]:
    """Admit the optional pair only when existing tools and the pair can fit."""
    if limit is None or not any(
        tool.get("function", {}).get("type") == SCHEDULED_ACTION_TOOL_TYPE
        for tool in tools
    ):
        return tools
    if count_model_function_tools(tools) + extra_tool_count <= limit:
        return tools
    return [
        tool
        for tool in tools
        if tool.get("function", {}).get("type") != SCHEDULED_ACTION_TOOL_TYPE
    ]


def conversation_scheduled_action_owner(
    agent: Any, context: llm.LLMContext | None
) -> ScheduledActionOwner | None:
    """Derive ownership from the HA request, never Voice Identity data scope."""
    if context is None or getattr(context, _DELAYED_EXECUTION_MARKER, False):
        return None
    user_id = getattr(getattr(context, "context", None), "user_id", None)
    device_id = getattr(context, "device_id", None)
    satellite_id = get_request_satellite()
    if not (user_id or device_id or satellite_id):
        return None
    return ScheduledActionOwner(
        agent.entry.entry_id,
        agent.subentry.subentry_id,
        user_id,
        device_id,
        satellite_id,
    )


async def async_execute_scheduled_action_tool(
    agent: Any,
    owner: ScheduledActionOwner | None,
    operation: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Check the live caller and Guest Mode before calling the shared scheduler."""
    if owner is None or agent._effective_guest_policy().guest_active:
        return {
            "status": "denied",
            "error": "Scheduled actions are unavailable for this caller.",
        }
    if owner.user_id is not None:
        user = await agent.hass.auth.async_get_user(owner.user_id)
        if (
            user is None
            or not user.is_active
            or agent._effective_guest_policy().guest_active
        ):
            return {
                "status": "denied",
                "error": "Scheduled actions are unavailable for this caller.",
            }
    if not (
        (operation == "list" and not arguments)
        or (
            operation == "cancel"
            and set(arguments) == {"action_id"}
            and valid_scheduled_action_id(arguments["action_id"])
        )
    ):
        raise HomeAssistantError(
            "Use an exact scheduled action ID for cancellation and no arguments for listing"
        )
    manager = delayed_tool_manager(agent.hass)
    if manager is None:
        return {"status": "unavailable"}

    def authorized() -> bool:
        return not agent._effective_guest_policy().guest_active

    if operation == "list":
        return await manager.async_list_scheduled_actions(owner, authorized=authorized)
    return await manager.async_cancel_scheduled_action(
        owner, arguments["action_id"], authorized=authorized
    )
