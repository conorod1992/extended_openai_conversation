"""Tests for versioned agent configuration import and export."""

from types import SimpleNamespace

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.agent_config import (
    GUEST_V2_FIELDS,
    AgentConfigError,
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    _export_agent,
    _parse_import_document,
    _redact_export_secrets,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL,
    redact_secrets,
    restore_redacted_secrets,
)


def test_export_is_versioned_and_excludes_history_and_credentials(hass) -> None:
    data = agent_config_defaults()
    data["prompt"] = "Custom"
    data["function_groups"] = [
        {
            "id": "general",
            "name": "General",
            "description": "General functions",
            "loading_mode": "always",
            "functions": [],
        }
    ]
    subentry = SimpleNamespace(title="Jarvis", data=data)
    document = _export_agent(subentry)
    assert document["version"] == 1
    assert document["config"]["prompt"] == "Custom"
    assert document["config"]["function_groups"][0]["id"] == "general"
    serialized = str(document)
    assert "api_key" not in serialized
    assert "memory_contents" not in serialized
    assert "usage_history" not in serialized


def test_export_redaction_preserves_schema_fields_but_removes_values() -> None:
    value = {
        "function": {
            "api_key": "secret",
            "apiKey": "secret",
            "clientSecret": "secret",
            "accessToken": "secret",
            "refreshToken": "secret",
            "headers": {"Authorization": "Bearer secret", "Accept": "json"},
        },
        "spec": {"parameters": {"properties": {"password": {"type": "string"}}}},
    }
    redacted = _redact_export_secrets(value)
    assert redacted["function"]["api_key"] == REDACTED_SECRET_SENTINEL
    assert redacted["function"]["apiKey"] == REDACTED_SECRET_SENTINEL
    assert redacted["function"]["clientSecret"] == REDACTED_SECRET_SENTINEL
    assert redacted["function"]["accessToken"] == REDACTED_SECRET_SENTINEL
    assert redacted["function"]["refreshToken"] == REDACTED_SECRET_SENTINEL
    assert (
        redacted["function"]["headers"]["Authorization"]
        == REDACTED_SECRET_SENTINEL
    )
    assert redacted["function"]["headers"]["Accept"] == "json"
    assert "password" in redacted["spec"]["parameters"]["properties"]


def test_import_applies_defaults_and_preserves_tools(hass) -> None:
    document = {
        "schema": "extended_openai_conversation.agent",
        "version": 1,
        "title": "Imported",
        "config": {"chat_model": "gpt-4o", "functions": []},
    }
    parsed = _parse_import_document(document)
    assert parsed["title"] == "Imported"
    assert parsed["config"]["functions"] == "[]\n"
    assert parsed["config"]["function_groups"] == []
    assert parsed["config"]["max_tokens"] > 0
    assert GUEST_V2_FIELDS.isdisjoint(parsed["config"])


def test_legacy_export_does_not_silently_accept_guest_v2(hass) -> None:
    data = agent_config_defaults()
    for key in GUEST_V2_FIELDS:
        data.pop(key, None)
    parsed = _parse_import_document(
        _export_agent(SimpleNamespace(title="Old", data=data))
    )
    assert GUEST_V2_FIELDS.isdisjoint(parsed["config"])


def test_import_export_round_trip_preserves_disabled_tool_state(hass) -> None:
    data = agent_config_defaults()
    data["functions"] = yaml.safe_dump(
        [
            {
                "enabled": False,
                "spec": {
                    "name": "energy",
                    "description": "Get energy",
                    "parameters": {"type": "object", "properties": {}},
                },
                "function": {"type": "native", "name": "get_energy"},
            }
        ]
    )
    exported = _export_agent(SimpleNamespace(title="Jarvis", data=data))
    parsed = _parse_import_document(exported)
    assert yaml.safe_load(parsed["config"]["functions"])[0]["enabled"] is False


def test_import_rejects_versions_unknown_fields_and_credentials() -> None:
    with pytest.raises(AgentConfigError, match="unsupported export version"):
        _parse_import_document(
            {
                "schema": "extended_openai_conversation.agent",
                "version": 99,
                "config": {},
            }
        )
    with pytest.raises(AgentConfigError, match="unknown fields"):
        _parse_import_document(
            {
                "schema": "extended_openai_conversation.agent",
                "version": 1,
                "config": {"api_key": "secret"},
            }
        )


def test_import_uses_title_contract_and_removes_redaction_sentinel() -> None:
    config = agent_config_defaults()
    config["voice_device_mappings"] = {"device": None}
    assert restore_redacted_secrets(config)["voice_device_mappings"]["device"] is None

    valid_config = agent_config_defaults()
    valid_config["api_key"] = REDACTED_SECRET_SENTINEL
    parsed = _parse_import_document(
        {
            "schema": "extended_openai_conversation.agent",
            "version": 1,
            "title": "  Imported  ",
            "config": valid_config,
        }
    )
    assert parsed["title"] == "Imported"
    assert "api_key" not in parsed["config"]

    with pytest.raises(AgentConfigError, match="must not be empty"):
        _parse_import_document(
            {
                "schema": "extended_openai_conversation.agent",
                "version": 1,
                "title": "   ",
                "config": agent_config_defaults(),
            }
        )


def test_redaction_preserves_structure_but_restore_drops_only_sentinel() -> None:
    original = {
        "actions": [
            {
                "action": "rest.call",
                "data": {
                    "Authorization": "Bearer secret",
                    "payload": None,
                    "nested": [None, {"api-key": "credential"}],
                },
            }
        ],
        "parameters": {
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
        },
    }
    redacted = redact_secrets(original)
    data = redacted["actions"][0]["data"]
    assert data["Authorization"] == REDACTED_SECRET_SENTINEL
    assert data["nested"][1]["api-key"] == REDACTED_SECRET_SENTINEL
    assert "api_key" in redacted["parameters"]["properties"]

    restored = restore_redacted_secrets(redacted)
    restored_data = restored["actions"][0]["data"]
    assert "Authorization" not in restored_data
    assert restored_data["payload"] is None
    assert restored_data["nested"] == [None, {}]
