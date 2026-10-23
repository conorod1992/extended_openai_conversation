"""Sensitive references require a proof of the actual schema target."""

from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from tests.test_sharing_schema_context import configuration_artifact
from tests.test_sharing_schema_references import tool_with_schema

SECRET = "FABRICATED-scoped-reference-password-42"


def assert_omitted(config, format):
    before = deepcopy(config)
    agent_config_snapshot(config)
    artifact, public, serialized = configuration_artifact(config, format)
    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]
    assert config == before


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("public_first", [False, True])
def test_nested_resources_cannot_reuse_a_different_sensitive_reference_proof(
    hass, format, relative, public_first
):
    login = {
        "$id": "login" if relative else "https://schema.example.invalid/login",
        "type": "object",
        "$defs": {"shared": {"type": "string", "const": SECRET}},
        "properties": {"password": {"$ref": "#/$defs/shared"}},
    }
    properties = {"login": login}
    if public_first:
        properties = {
            "public_login": {
                "$id": "https://schema.example.invalid/public-login",
                "type": "object",
                "$defs": {"shared": {"type": "string"}},
                "properties": {"password": {"$ref": "#/$defs/shared"}},
            },
            **properties,
        }
    schema = {
        "type": "object",
        "$defs": {"shared": {"type": "string"}},
        "properties": properties,
    }
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"login": {"password": SECRET}})
    assert not validator.is_valid({"login": {"password": "wrong"}})
    assert_omitted({"functions": [tool_with_schema(schema)]}, format)


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize(
    ("name", "pointer"),
    [
        ("a b", "#/$defs/a%20b"),
        ("café", "#/$defs/caf%C3%A9"),
        ("a/b", "#/$defs/a%7E1b"),
        ("a%20b", "#/$defs/a%2520b"),
    ],
)
def test_uri_fragment_decoding_precedes_json_pointer_target_checks(
    hass, format, name, pointer
):
    # A harmless literal-encoded key must not shadow the real decoded target.
    schema = {
        "type": "object",
        "$defs": {
            name: {"type": "string", "const": SECRET},
            pointer.removeprefix("#/$defs/"): {"type": "string"},
        },
        "properties": {"password": {"$ref": pointer}},
        "examples": [{"id": "ordinary-instance-id"}],
    }
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"password": SECRET})
    assert not validator.is_valid({"password": "wrong"})
    assert_omitted({"functions": [tool_with_schema(schema)]}, format)


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("pointer", ["#/$defs/plain", "#/$defs/a%20b"])
def test_supported_public_local_references_stay_portable(hass, format, pointer):
    schema = {
        "$id": "https://schema.example.invalid/root",
        "type": "object",
        "$defs": {"plain": {"type": "string"}, "a b": {"type": "string"}},
        "properties": {"password": {"$ref": pointer}},
        "examples": [{"id": "ordinary-instance-id"}],
    }
    tool = tool_with_schema(schema)
    _artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == [tool]


def test_nested_resource_without_sensitive_references_stays_portable(hass):
    tool = tool_with_schema(
        {
            "type": "object",
            "properties": {
                "units": {
                    "$id": "https://schema.example.invalid/units",
                    "type": "string",
                    "enum": ["c", "f"],
                }
            },
        }
    )
    _artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, "setup"
    )
    assert public["functions"] == [tool]


@pytest.mark.parametrize(
    "pointer",
    ["#/$defs/bad%ZZ", "#/$defs/bad%FF", "#/$defs/bad~2", "#%2F$defs%2Fplain"],
)
def test_unprovable_pointer_encodings_are_omitted(hass, pointer):
    schema = {
        "type": "object",
        "$defs": {pointer.removeprefix("#/$defs/"): {"type": "string"}},
        "properties": {"password": {"$ref": pointer}},
    }
    assert_omitted({"functions": [tool_with_schema(schema)]}, "setup")
