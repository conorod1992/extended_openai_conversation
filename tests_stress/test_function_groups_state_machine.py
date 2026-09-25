"""Seeded multi-session on-demand Function Group inventory model."""

from __future__ import annotations

import random

from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
    assemble_function_tools,
    load_function_groups,
)
from tests_stress.conftest import record


def _tool(name: str) -> dict:
    return {
        "spec": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
        "enabled": True,
    }


def test_seeded_group_loading_respects_current_config_and_session_scope(
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    rng = random.Random(stress_seed ^ 0xF00C)
    runtime = FunctionGroupRuntime()
    tools = [_tool(f"tool-{number}") for number in range(24)]
    groups = [
        {
            "id": f"group-{number}",
            "name": f"Group {number}",
            "description": "Nightly group",
            "loading_mode": "always" if number % 4 == 0 else "on_demand",
            "functions": [f"tool-{number * 2}", f"tool-{number * 2 + 1}"],
            "enabled": True,
        }
        for number in range(12)
    ]
    expected_loaded: dict[str, set[str]] = {}
    sessions = [f"conversation-{number}" for number in range(16)]
    for _number in range(180 * stress_scale):
        key = rng.choice(sessions)
        session = runtime.begin(key, 60)
        expected = expected_loaded.setdefault(key, set())
        operation = rng.choice(
            ("load", "load", "group_toggle", "tool_toggle", "end", "inspect")
        )
        if operation == "load":
            group = rng.choice(groups)
            record(stress_trace, operation, session=key, group=group["id"])
            # Loading revalidates all previously loaded groups for this exact
            # session before considering the requested group.
            expected.intersection_update(
                current["id"]
                for current in groups
                if current["enabled"]
                and current["loading_mode"] == "on_demand"
                and any(
                    tool["enabled"]
                    for tool in tools
                    if tool["spec"]["name"] in current["functions"]
                )
            )
            load_function_groups(session, [group["id"]], groups, tools)
            if (
                group["enabled"]
                and group["loading_mode"] == "on_demand"
                and any(
                    tool["enabled"]
                    for tool in tools
                    if tool["spec"]["name"] in group["functions"]
                )
            ):
                expected.add(group["id"])
        elif operation == "group_toggle":
            group = rng.choice(groups)
            group["enabled"] = not group["enabled"]
            record(stress_trace, operation, group=group["id"], enabled=group["enabled"])
        elif operation == "tool_toggle":
            tool = rng.choice(tools)
            tool["enabled"] = not tool["enabled"]
            record(
                stress_trace,
                operation,
                tool=tool["spec"]["name"],
                enabled=tool["enabled"],
            )
        elif operation == "end":
            record(stress_trace, operation, session=key)
            assert runtime.end(key)
            expected_loaded.pop(key, None)
            session = runtime.begin(key, 60)
            expected = expected_loaded.setdefault(key, set())
        else:
            record(stress_trace, operation, session=key)

        for check_key in rng.sample(sessions, 4):
            check = runtime.begin(check_key, 60)
            model_loaded = expected_loaded.setdefault(check_key, set())
            available_on_demand = {
                group["id"]
                for group in groups
                if group["enabled"]
                and group["loading_mode"] == "on_demand"
                and any(
                    tool["enabled"]
                    for tool in tools
                    if tool["spec"]["name"] in group["functions"]
                )
            }
            model_loaded.intersection_update(available_on_demand)
            assembly = assemble_function_tools(tools, groups, check.loaded_group_ids)
            actual_names = {tool["spec"]["name"] for tool in assembly.tools}
            expected_names = {
                tool["spec"]["name"]
                for tool in tools
                if tool["enabled"]
                for group in groups
                if tool["spec"]["name"] in group["functions"]
                and group["enabled"]
                and (group["loading_mode"] == "always" or group["id"] in model_loaded)
            }
            if available_on_demand - model_loaded:
                expected_names.add("load_function_groups")
            assert actual_names == expected_names
            assert check.loaded_group_ids == model_loaded
            assert len(assembly.tools) == len(actual_names)
    record(
        stress_trace,
        "summary",
        operations=180 * stress_scale,
        groups=len(groups),
        tools=len(tools),
        conversations=len(sessions),
    )
