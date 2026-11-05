"""Shared Guest function/group projection for live requests and diagnostics."""

from typing import Any

from .guest_mode import GuestCapabilityPolicy
from .ha_llm_tools import is_ha_tool


def filter_guest_tools_and_groups(
    configured_tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    policy: GuestCapabilityPolicy,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the resolved Guest function policy without orphaning members."""
    if not policy.guest_active:
        return configured_tools, groups
    membership = {
        name: group for group in groups for name in group.get("functions", [])
    }
    configured_tools = [
        tool
        for tool in configured_tools
        if not is_ha_tool(tool)
        and policy.allows_configured_tool(tool["spec"]["name"])
        and (
            not policy.legacy_function_flags
            or (
                tool["spec"]["name"] not in membership
                or membership[tool["spec"]["name"]].get("guest_allowed") is True
            )
        )
    ]
    allowed_names = {tool["spec"]["name"] for tool in configured_tools}
    guest_groups = [
        {
            **group,
            "functions": [name for name in group["functions"] if name in allowed_names],
        }
        for group in groups
        if (not policy.legacy_function_flags or group.get("guest_allowed") is True)
        and any(name in allowed_names for name in group.get("functions", []))
    ]
    return configured_tools, guest_groups
