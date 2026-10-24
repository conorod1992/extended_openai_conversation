"""Sharing treats reference vocabularies as schema authority, never public prose."""

from copy import deepcopy
import json

from jsonschema import Draft201909Validator, Draft202012Validator
import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_secret_restore_context import _rest_tool
from tests.test_sharing_schema_context import configuration_artifact

SECRET = "FABRICATED-unfamiliar-schema-credential-42"
REFERENCES = ["$ref", "$dynamicRef", "$recursiveRef"]


def tool_with_schema(schema):
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    tool["spec"]["parameters"] = schema
    return tool


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("reference", REFERENCES)
def test_sensitive_reference_constants_do_not_leave_real_artifacts(
    hass, format, reference
):
    # The constant is the password's value, even though it lives in a shared def.
    schema = {
        "type": "object",
        "properties": {"password": {reference: "#/$defs/shared"}},
        "$defs": {"shared": {"type": "string", "const": SECRET}},
    }
    if reference == "$recursiveRef":
        # 2019-09 recursive references resolve through the dynamic root anchor.
        schema = {
            "$recursiveAnchor": True,
            "type": "object",
            "properties": {"password": {"$recursiveRef": "#"}},
            "default": {"password": SECRET},
        }
        Draft201909Validator.check_schema(schema)
    else:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        assert validator.is_valid({"password": SECRET})
        assert not validator.is_valid({"password": "wrong"})
    config = {"functions": [tool_with_schema(schema)]}
    before = deepcopy(config)
    agent_config_snapshot(config)

    artifact, public, serialized = configuration_artifact(config, format)

    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]
    assert config == before


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("reference", REFERENCES)
@pytest.mark.parametrize("location", ["root", "sensitive_property"])
@pytest.mark.parametrize(
    "uri",
    [
        "https://schema.example.invalid/shared?sig=",
        "//schema.example.invalid/shared?sig=",
        "shared?sig=",
        "?sig=",
        "custom://schema.example.invalid/shared?sig=",
        "https://user:{secret}@schema.example.invalid/shared",
    ],
)
def test_reference_urls_with_known_credentials_are_never_shared(
    hass, format, reference, location, uri
):
    schema = {
        reference: uri.format(secret=SECRET) if "{secret}" in uri else uri + SECRET
    }
    if location == "sensitive_property":
        schema = {"type": "object", "properties": {"password": schema}}
    config = {"functions": [tool_with_schema(schema)]}
    agent_config_snapshot(config)

    artifact, public, serialized = configuration_artifact(config, format)

    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]


@pytest.mark.parametrize("reference", ["$dynamicRef", "$recursiveRef"])
@pytest.mark.parametrize(
    "target", ["#", "#node", "#/$defs/loop", "https://example.invalid/schema"]
)
def test_sensitive_dynamic_or_recursive_scope_is_conservatively_omitted(
    hass, reference, target
):
    schema = {
        "type": "object",
        "properties": {"password": {reference: target}},
        "$defs": {"loop": {reference: "#/$defs/loop"}},
    }
    artifact, public, _serialized = configuration_artifact(
        {"functions": [tool_with_schema(schema)]}, "setup"
    )
    assert public["functions"] == []
    assert artifact["security"]["omitted"]


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("reference", REFERENCES)
def test_public_reference_schemas_remain_portable(hass, format, reference):
    schema = {
        "type": "object",
        "properties": {"units": {reference: "#/$defs/public"}},
        "$defs": {"public": {"type": "string", "enum": ["c", "f"]}},
    }
    tool = tool_with_schema(schema)
    artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == [tool]
    if format == "setup":
        transfer.inspect_transfer(artifact, "new-agent")


@pytest.mark.parametrize("credential", [False, True])
def test_compatibility_example_is_instance_data(hass, credential):
    schema = {
        "type": "object",
        "properties": {
            "password" if credential else "units": {
                "type": "string",
                "example": SECRET if credential else "c",
            }
        },
    }
    tool = tool_with_schema(schema)
    artifact, public, serialized = configuration_artifact(
        {"functions": [tool]}, "setup"
    )
    assert SECRET not in serialized
    assert public["functions"] == ([] if credential else [tool])
    assert bool(artifact["security"]["omitted"]) is credential


@pytest.mark.parametrize("reference", REFERENCES)
def test_private_backup_preserves_reference_credentials_exactly(hass, reference):
    document = private_document()
    schema = {
        "type": "object",
        "properties": {"password": {reference: "#/$defs/shared"}},
        "$defs": {"shared": {"type": "string", "const": SECRET}},
    }
    document["agent"]["config"]["functions"] = [tool_with_schema(schema)]
    before = deepcopy(document)
    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])

    assert decode_document(artifact) == before
    prepared = transfer.inspect_transfer(artifact, "new-agent")
    assert prepared.config == before["agent"]["config"]
    assert prepared.provider_connection == before["provider_connection"]
