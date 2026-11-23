"""Schema vocabulary URI keys receive the same policy as URI values."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from tests.test_private_backup_fidelity import private_document
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
