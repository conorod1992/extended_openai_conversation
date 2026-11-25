"""Sharing treats reference vocabularies as schema authority, never public prose."""

from copy import deepcopy
import json

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_secret_restore_context import _rest_tool

SECRET = "FABRICATED-unfamiliar-schema-credential-42"
REFERENCES = ["$ref", "$dynamicRef", "$recursiveRef"]


def tool_with_schema(schema):
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    tool["spec"]["parameters"] = schema
    return tool


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
