"""Omitting private tools cannot promote safe tools out of their availability groups."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    assemble_function_tools,
)
from custom_components.extended_openai_conversation_responses.sharing import (
    SHARING_VERSION,
)
from tests.test_function_groups import _group, _tool
from tests.test_secret_restore_context import _rest_tool
from tests.test_transfer import _portable_document


def _roundtrip(config, kind):
    if kind == "setup":
        document = _portable_document(mode="setup")
        document["version"] = SHARING_VERSION
        document["sections"]["configuration"] = deepcopy(config)
        artifact = transfer.finalize_setup_export(document, reviewed=True)["document"]
        return transfer.inspect_transfer(artifact, "target").config, artifact
    artifact = management_ui._export_agent(SimpleNamespace(data=config), reviewed=True)
    destination = SimpleNamespace(data=deepcopy(config)) if kind == "current" else None
    return management_ui._parse_import_document(artifact, destination=destination)[
        "config"
    ], artifact


@pytest.mark.parametrize("kind", ["new", "current", "setup"])
@pytest.mark.parametrize(
    "mode,enabled", [("always", False), ("on_demand", True), ("always", True)]
)
@pytest.mark.parametrize("private_member", [False, True])
@pytest.mark.parametrize("omit", [False, True])
def test_reviewed_export_preserves_surviving_group_availability(
    hass, kind, mode, enabled, private_member, omit
):
    held = {**_tool("held_tool"), "enabled": True}
    private = _rest_tool()
    private["spec"]["name"] = "private_api"
    if not omit:
        private["function"]["headers"] = {"Accept": "application/json"}
    members = ["held_tool", "private_api"] if private_member else ["held_tool"]
    config = {
        "functions": [held, private],
        "function_groups": [_group("held", members, mode, enabled=enabled)],
    }
    before = agent_config_snapshot(config)
    imported, artifact = _roundtrip(config, kind)
    after = agent_config_snapshot(imported)
    assert after["function_groups"] == [
        {
            **before["function_groups"][0],
            "functions": [
                name for name in members if name != "private_api" or not omit
            ],
        }
    ]
    assert (
        next(
            tool for tool in after["functions"] if tool["spec"]["name"] == "held_tool"
        )["enabled"]
        is True
    )
    names = {
        tool["spec"]["name"]
        for tool in assemble_function_tools(
            after["functions"], after["function_groups"], set()
        ).tools
    }
    assert ("held_tool" in names) is (enabled and mode == "always")
    assert ("load_function_groups" in names) is (enabled and mode == "on_demand")
    assert ("private_api" in {tool["spec"]["name"] for tool in after["functions"]}) is (
        not omit
    )
    assert (
        any(
            item["field"].startswith("function_tool_")
            for item in artifact["security"]["omitted"]
        )
        is omit
    )


@pytest.mark.parametrize("kind", ["new", "current", "setup"])
def test_unsafe_group_metadata_disables_members_and_reports_change(hass, kind):
    group = _group("held", ["held_tool"], enabled=False)
    group["description"] = "Authorization: Bearer sk-FABRICATED123456789012345"
    imported, artifact = _roundtrip(
        {
            "functions": [{**_tool("held_tool"), "enabled": True}],
            "function_groups": [group],
        },
        kind,
    )
    snapshot = agent_config_snapshot(imported)
    assert not assemble_function_tools(
        snapshot["functions"], snapshot["function_groups"], set()
    ).tools
    assert snapshot["functions"][0]["enabled"] is False
    assert any(
        item["field"] == "function_group_member_disabled"
        for item in artifact["security"]["omitted"]
    )
