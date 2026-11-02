"""Shared YAML schemas stay independent through startup persistence/reload."""

from copy import deepcopy
import json
from types import SimpleNamespace

import yaml

from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.native_function_schema_migration import (
    _LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS,
)
from tests.test_function_quarantine_roundtrip import _restart_and_migrate


async def test_startup_persisted_alias_does_not_change_custom_calls(hass):
    shared = deepcopy(_LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS)
    native = {
        "spec": {"name": "native_service", "parameters": shared},
        "function": {"type": "native", "name": "execute_service"},
    }
    custom = {
        "spec": {"name": "custom_template", "parameters": shared},
        "function": {"type": "template", "value_template": "{{ list | default([]) }}"},
    }
    source = {
        "functions": yaml.safe_dump([native, custom], sort_keys=False),
        "function_groups": [],
    }
    assert "&id" in source["functions"]
    before = configured_function_tools_from_data(source)[1]
    validate_function_arguments(before["spec"], {})
    restarted, affected, _healthy, writes = _restart_and_migrate(
        SimpleNamespace(data=source)
    )
    await integration.async_migrate_integration(restarted)
    # Drop identities through HA's persisted JSON boundary, then reload Tools.
    saved = json.loads(json.dumps(affected.data))
    after = configured_function_tools_from_data(saved)[1]
    assert after["spec"] == before["spec"]
    validate_function_arguments(after["spec"], {})
    assert configured_function_tools_from_data(saved)[0]["spec"]["strict"] is False
    assert writes == ["restored", "healthy"]
    await integration.async_migrate_integration(restarted)
    assert affected.data == saved and writes == ["restored", "healthy"]
