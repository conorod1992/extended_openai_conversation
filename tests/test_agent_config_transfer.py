"""Tests for versioned agent configuration import and export."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    _export_agent,
    _parse_import_document,
    _redact_export_secrets,
)


def test_export_is_versioned_and_excludes_history_and_credentials(hass) -> None:
    data = agent_config_defaults()
    data["prompt"] = "Custom"
    subentry = SimpleNamespace(title="Jarvis", data=data)
    document = _export_agent(subentry)
    assert document["version"] == 1
    assert document["config"]["prompt"] == "Custom"
    serialized = str(document)
    assert "api_key" not in serialized
    assert "memory_contents" not in serialized
    assert "usage_history" not in serialized


def test_export_redaction_preserves_schema_fields_but_removes_values() -> None:
    value = {
        "function": {
            "api_key": "secret",
            "headers": {"Authorization": "Bearer secret", "Accept": "json"},
        },
        "spec": {"parameters": {"properties": {"password": {"type": "string"}}}},
    }
    redacted = _redact_export_secrets(value)
    assert "api_key" not in redacted["function"]
    assert "Authorization" not in redacted["function"]["headers"]
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
    assert parsed["config"]["max_tokens"] > 0


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
