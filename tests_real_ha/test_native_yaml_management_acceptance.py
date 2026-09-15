"""Real-HA acceptance for the Function Tool YAML editor backend contract."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _management_call,
    _setup_entry,
)


def _tool_yaml(description: str) -> str:
    return f"""spec:
  name: native_yaml_acceptance
  description: {description}
  parameters:
    type: object
    properties: {{}}
function:
  type: native
  name: get_user_from_user_id
"""


def _find_tool(configuration: dict[str, Any]) -> dict[str, Any]:
    return next(
        tool
        for tool in configuration["config"]["functions"]
        if tool["spec"]["name"] == "native_yaml_acceptance"
    )


@pytest.mark.asyncio
async def test_native_yaml_validate_save_readback_atomic_rejection_and_recovery(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """The editor's validate/save contract stays atomic across invalid YAML."""
    entry = _entry("Native YAML Backend Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(
        hass,
        hass_ws_client,
        user_id="native-yaml-management-acceptance",
        name="Native YAML Management Acceptance",
    )

    created_yaml = _tool_yaml("Native YAML backend acceptance created")
    created_validation = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="validate_yaml",
        yaml=created_yaml,
    )
    assert created_validation["valid"] is True
    assert created_validation["config"]["spec"]["name"] == "native_yaml_acceptance"

    await _management_call(
        client,
        entry=entry,
        section="tools",
        action="save",
        tool=created_validation["config"],
    )
    await hass.async_block_till_done()

    configuration = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert _find_tool(configuration)["spec"]["description"] == (
        "Native YAML backend acceptance created"
    )

    serialized = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="serialize",
        tool=_find_tool(configuration),
    )
    assert "name: native_yaml_acceptance" in serialized["yaml"]

    edited_yaml = _tool_yaml("Native YAML backend acceptance edited")
    edited_validation = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="validate_yaml",
        yaml=edited_yaml,
    )
    assert edited_validation["valid"] is True
    await _management_call(
        client,
        entry=entry,
        section="tools",
        action="save",
        tool=edited_validation["config"],
        original_name="native_yaml_acceptance",
    )
    await hass.async_block_till_done()

    edited_configuration = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert _find_tool(edited_configuration)["spec"]["description"] == (
        "Native YAML backend acceptance edited"
    )

    # This is syntactically valid YAML but an invalid Function Tool. Validation
    # must not partially replace the already-saved tool.
    invalid_yaml = """spec:
  description: Invalid YAML editor attempt
  parameters:
    type: object
    properties: {}
function:
  type: native
  name: get_user_from_user_id
"""
    invalid_validation = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="validate_yaml",
        yaml=invalid_yaml,
    )
    assert invalid_validation["valid"] is False
    assert invalid_validation["errors"]

    after_invalid = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert _find_tool(after_invalid)["spec"]["description"] == (
        "Native YAML backend acceptance edited"
    )

    recovered_yaml = _tool_yaml("Native YAML backend acceptance recovered")
    recovered_validation = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="validate_yaml",
        yaml=recovered_yaml,
    )
    assert recovered_validation["valid"] is True
    await _management_call(
        client,
        entry=entry,
        section="tools",
        action="save",
        tool=recovered_validation["config"],
        original_name="native_yaml_acceptance",
    )
    await hass.async_block_till_done()

    recovered_configuration = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert _find_tool(recovered_configuration)["spec"]["description"] == (
        "Native YAML backend acceptance recovered"
    )

    await _management_call(
        client,
        entry=entry,
        section="tools",
        action="delete",
        name="native_yaml_acceptance",
        confirm=True,
    )
