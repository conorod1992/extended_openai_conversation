"""Schema identities and dialect URIs share the reference credential policy."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

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

SECRET = "FABRICATED-private-schema-uri-42"


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("keyword", ["$id", "$schema", "id"])
@pytest.mark.parametrize("credential", ["signature", "userinfo"])
def test_schema_uri_credentials_are_omitted_from_actual_artifacts(
    hass, format, keyword, credential
):
    uri = (
        "https://schema.example.invalid/document?sig=" + SECRET
        if credential == "signature"
        else "https://user:" + SECRET + "@schema.example.invalid/document"
    )
    schema = {"type": "object", keyword: uri, "properties": {}}
    Draft202012Validator.check_schema(schema)
    config = {"functions": [tool_with_schema(schema)]}
    before = deepcopy(config)
    agent_config_snapshot(config)

    artifact, public, serialized = configuration_artifact(config, format)

    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]
    assert config == before


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("keyword", ["$id", "$schema", "id"])
def test_public_schema_uri_metadata_stays_portable(hass, format, keyword):
    tool = tool_with_schema(
        {
            "type": "object",
            keyword: "https://schema.example.invalid/public",
            "properties": {},
        }
    )
    _artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == [tool]


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("mapping", ["$defs", "definitions", "dependentSchemas"])
@pytest.mark.parametrize("name", ["$id", "$schema", "$ref", "id"])
def test_schema_map_names_are_not_uri_keyword_values(hass, format, mapping, name):
    # Named schema entries are labels, including names colliding with keywords.
    schema = {"type": "object", mapping: {name: {"type": "string"}}}
    Draft202012Validator.check_schema(schema)
    tool = tool_with_schema(schema)
    _artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == [tool]


@pytest.mark.parametrize("keyword", ["$id", "$schema", "id"])
def test_private_schema_uri_values_remain_exact(hass, keyword):
    document = private_document()
    document["agent"]["config"]["functions"] = [
        tool_with_schema(
            {"type": "object", keyword: "https://schema.example.invalid/?sig=" + SECRET}
        )
    ]
    before = deepcopy(document)
    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])
    assert decode_document(artifact) == before
    prepared = transfer.inspect_transfer(artifact, "new-agent")
    assert prepared.config == before["agent"]["config"]
    assert prepared.provider_connection == before["provider_connection"]
