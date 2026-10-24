"""Schema vocabulary URI keys receive the same policy as URI values."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_sharing_schema_context import configuration_artifact
from tests.test_sharing_schema_references import tool_with_schema

SECRET = "FABRICATED-vocabulary-credential-42"
PRIVATE_URIS = [
    "https://schema.example.invalid/vocabulary?sig=" + SECRET,
    "https://user:" + SECRET + "@schema.example.invalid/vocabulary",
    "custom://schema.example.invalid/vocabulary?signature=" + SECRET,
]


def vocabulary_tool(uri, nested=False):
    vocabulary = {"$vocabulary": {uri: True}, "type": "object", "properties": {}}
    schema = (
        {"type": "object", "$defs": {"shared": vocabulary}} if nested else vocabulary
    )
    Draft202012Validator.check_schema(schema)
    return tool_with_schema(schema)


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("uri", PRIVATE_URIS)
def test_vocabulary_credentials_are_omitted_from_artifacts(
    hass, format, persisted, nested, uri
):
    tool = vocabulary_tool(uri, nested)
    config = {"functions": yaml.safe_dump([tool]) if persisted else [tool]}
    before = deepcopy(config)
    agent_config_snapshot(config)

    artifact, public, serialized = configuration_artifact(config, format)

    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]
    assert config == before


@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_public_vocabulary_maps_and_keyword_named_definitions_stay_portable(
    hass, format
):
    tool = vocabulary_tool("https://schema.example.invalid/required")
    schema = tool["spec"]["parameters"]
    schema["$vocabulary"]["https://schema.example.invalid/optional"] = False
    schema["$defs"] = {"$vocabulary": {"type": "string"}}
    Draft202012Validator.check_schema(schema)
    config = {"functions": [tool]}
    agent_config_snapshot(config)

    artifact, public, _serialized = configuration_artifact(config, format)

    assert public["functions"] == [tool]
    if format == "setup":
        transfer.inspect_transfer(artifact, "new-agent")


@pytest.mark.parametrize(
    "value", [None, [], "custom", {"custom": {"password": SECRET}}]
)
def test_unclassified_vocabulary_maps_are_omitted(hass, value):
    tool = tool_with_schema({"type": "object", "$vocabulary": value})
    artifact, public, serialized = configuration_artifact(
        {"functions": [tool]}, "setup"
    )
    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]


@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("uri", PRIVATE_URIS)
def test_private_backup_keeps_vocabulary_credentials_exactly(hass, persisted, uri):
    document = private_document()
    tool = vocabulary_tool(uri)
    document["agent"]["config"]["functions"] = (
        yaml.safe_dump([tool]) if persisted else [tool]
    )
    before = deepcopy(document)

    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])

    assert decode_document(artifact) == before
    prepared = transfer.inspect_transfer(artifact, "new-agent")
    assert prepared.config == before["agent"]["config"]
    assert prepared.provider_connection == before["provider_connection"]
    assert document == before
