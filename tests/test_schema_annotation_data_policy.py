"""Only declared schema locations grant structural name exemptions."""

from copy import deepcopy
import json
import string

from hypothesis import given, settings, strategies as st
from jsonschema import Draft7Validator, Draft202012Validator
import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    PRIVATE_TRANSFER_VERSION,
    decode_document,
    encode_document,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_sharing_schema_context import configuration_artifact
from tests.test_sharing_schema_references import tool_with_schema

SECRET = "FABRICATED-custom-schema-key-credential-91"
ANNOTATIONS = [
    {"api_key": SECRET},
    {"x-authentication": {"api_key": SECRET}},
    {"x-metadata": {"headers": {"Authorization": SECRET}}},
    {"x-metadata": [{"nested": {"clientSecret": SECRET}}]},
    {"x-request": {"payload": json.dumps({"password": SECRET})}},
    # A data object cannot reacquire schema privileges by using schema keywords.
    {"x-content": {"properties": {"password": {"const": SECRET}}}},
    {"x-content": {"functions": [{"spec": {"parameters": {"password": SECRET}}}]}},
]


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("annotation", ANNOTATIONS)
def test_annotation_credentials_are_omitted_from_real_artifacts(
    hass, format, persisted, annotation
):
    schema = {"type": "object", "properties": {}, **annotation}
    Draft202012Validator.check_schema(schema)
    tool = tool_with_schema(schema)
    config = {"functions": yaml.safe_dump([tool]) if persisted else [tool]}
    before = deepcopy(config)
    agent_config_snapshot(config)
    artifact, public, serialized = configuration_artifact(config, format)
    assert SECRET not in serialized
    assert public["functions"] == []
    assert any(
        item["field"] == "function_tool_1" for item in artifact["security"]["omitted"]
    )
    assert config == before


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
            "required": ["api_key"],
        },
        {
            "type": "object",
            "properties": {},
            "x-public": {
                "labels": ["one", "two"],
                "units": "c",
                "uri": "https://example.invalid/docs?units=c",
            },
        },
        {
            "type": "object",
            "properties": {"password": {"type": "string"}},
            "dependentRequired": {"password": ["units"], "$id": ["password"]},
        },
        {
            "type": "object",
            "properties": {},
            "dependencies": {"password": ["units"], "$id": ["password"]},
        },
    ],
)
def test_structural_names_and_public_annotation_data_stay_portable(
    hass, format, schema
):
    tool = tool_with_schema(schema)
    agent_config_snapshot({"functions": [tool]})
    _artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == [tool]


@pytest.mark.parametrize(
    "keyword",
    [
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "contains",
        "additionalItems",
        "unevaluatedItems",
        "not",
        "if",
        "then",
        "else",
        "contentSchema",
        "items",
    ],
)
@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_declared_single_subschemas_keep_structural_names(hass, keyword, format):
    child_type = (
        "array"
        if keyword in {"contains", "additionalItems", "unevaluatedItems", "items"}
        else "string"
        if keyword == "contentSchema"
        else "object"
    )
    schema = {
        "type": "object",
        "properties": {
            "container": {
                "type": child_type,
                keyword: {
                    "type": "object",
                    "properties": {"password": {"type": "string"}},
                },
            }
        },
    }
    # Some keywords are from older dialects; both accept the author's structure.
    Draft7Validator.check_schema(schema)
    Draft202012Validator.check_schema(schema)
    tool = tool_with_schema(schema)
    _artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == [tool]


@pytest.mark.parametrize("keyword", ["allOf", "anyOf", "oneOf", "prefixItems", "items"])
@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_declared_schema_arrays_keep_structural_names(hass, keyword, format):
    schema = {
        "type": "object",
        "properties": {},
        keyword: [{"type": "object", "properties": {"password": {"type": "string"}}}],
    }
    if keyword == "items":
        # The local validator does not support tuple items directly. A valid
        # composed legacy schema is intentionally transported as authored.
        schema = {
            "type": "object",
            "properties": {},
            "allOf": [
                {
                    "type": "object",
                    "properties": {"rows": {"type": "array", "items": schema["items"]}},
                }
            ],
        }
        Draft7Validator.check_schema(schema)
    else:
        Draft202012Validator.check_schema(schema)
    tool = tool_with_schema(schema)
    _artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == [tool]


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("keyword", ["$id", "$ref", "$schema"])
def test_annotation_data_retains_defensive_uri_checks(hass, format, keyword):
    tool = tool_with_schema(
        {
            "type": "object",
            "properties": {},
            "x-content": {keyword: "?access_token=" + SECRET},
        }
    )
    _artifact, public, serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == []
    assert SECRET not in serialized


@pytest.mark.parametrize("annotation", ANNOTATIONS)
def test_private_annotation_values_are_restored_exactly(hass, annotation):
    document = private_document()
    document["agent"]["config"]["functions"] = yaml.safe_dump(
        [tool_with_schema({"type": "object", "properties": {}, **annotation})]
    )
    before = deepcopy(document)
    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])
    assert decode_document(artifact) == before
    assert (
        transfer.inspect_transfer(artifact, "fresh-target").config
        == before["agent"]["config"]
    )
    custom = {
        "format": transfer.TRANSFER_FORMAT,
        "version": PRIVATE_TRANSFER_VERSION,
        "mode": "custom",
        "agent": {
            key: value for key, value in before["agent"].items() if key != "config"
        },
        "created_at": before["created_at"],
        "integration_version": before["integration_version"],
        "sections": {"configuration": before["agent"]["config"]},
        "provider_connection": before["provider_connection"],
    }
    custom_artifact = json.loads(json.dumps(encode_document(custom)))
    assert decode_document(custom_artifact) == custom
    assert (
        transfer.inspect_transfer(custom_artifact, "fresh-target").config
        == before["agent"]["config"]
    )


@settings(max_examples=30, deadline=None)
@given(
    st.text(
        alphabet=string.ascii_letters + string.digits + "_-.~", min_size=4, max_size=35
    )
)
def test_designated_values_do_not_gain_privileges_inside_custom_annotations(fabricated):
    for format in ("setup", "configuration"):
        for annotation in (
            {"default": {"api_key": fabricated}},
            {"x-future": {"api_key": fabricated}},
        ):
            tool = tool_with_schema({"type": "object", "properties": {}, **annotation})
            _artifact, public, _serialized = configuration_artifact(
                {"functions": [tool]}, format
            )
            assert public["functions"] == []
