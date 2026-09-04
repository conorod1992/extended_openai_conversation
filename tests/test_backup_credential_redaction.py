"""Focused tests for backup credential-key redaction."""

import pytest

from custom_components.extended_openai_conversation_responses.backup import (
    _safe_configuration,
)


@pytest.mark.parametrize(
    "secret_key",
    [
        "api_key",
        "API-Key",
        "API Key",
        "X-API-Key",
        "X API KEY",
        "clientSecret",
        "client-secret",
        "Client Secret",
        "accessToken",
        "access-token",
        "refresh token",
        "Authorization",
        "authorization-token",
        "X-Auth-Token",
    ],
)
def test_backup_redacts_separator_and_case_variants(secret_key: str) -> None:
    """Credential families are recognised independently of separator spelling."""
    safe = _safe_configuration(
        {
            "functions": [
                {
                    "function": {
                        "type": "rest",
                        "request": {
                            "headers": {
                                "Content-Type": "application/json",
                                secret_key: "credential-value",
                            }
                        },
                    }
                }
            ]
        }
    )

    headers = safe["functions"][0]["function"]["request"]["headers"]
    assert secret_key not in headers
    assert headers["Content-Type"] == "application/json"


def test_backup_redacts_nested_function_tool_credentials() -> None:
    """Nested custom Function Tool configuration cannot hide credential keys."""
    safe = _safe_configuration(
        {
            "functions": [
                {
                    "function": {
                        "type": "rest",
                        "options": {
                            "transport": {
                                "headers": {"X-API-Key": "header-secret"},
                                "credentials": {
                                    "Client Secret": "client-secret-value",
                                    "refresh-token": "refresh-secret-value",
                                },
                            }
                        },
                    }
                }
            ]
        }
    )

    transport = safe["functions"][0]["function"]["options"]["transport"]
    assert transport["headers"] == {}
    assert transport["credentials"] == {}


def test_backup_preserves_credential_named_schema_properties() -> None:
    """Credential-like property names remain valid inside JSON Schema definitions."""
    property_names = (
        "password",
        "X-API-Key",
        "Client Secret",
        "access-token",
    )
    safe = _safe_configuration(
        {
            "functions": [
                {
                    "spec": {
                        "name": "example",
                        "description": "Example schema",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                name: {"type": "string"} for name in property_names
                            },
                        },
                    },
                    "function": {"type": "native", "name": "execute_service"},
                }
            ]
        }
    )

    properties = safe["functions"][0]["spec"]["parameters"]["properties"]
    assert set(properties) == set(property_names)


def test_backup_secret_key_classification_avoids_substring_false_positives() -> None:
    """Ordinary words containing secret-like substrings are not removed."""
    safe = _safe_configuration(
        {
            "tokenizer": "keep",
            "secretary": "keep",
            "public-key": "keep",
            "monkey": "keep",
        }
    )

    assert safe == {
        "tokenizer": "keep",
        "secretary": "keep",
        "public-key": "keep",
        "monkey": "keep",
    }
