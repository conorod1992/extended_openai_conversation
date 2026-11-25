"""Only declared schema locations grant structural name exemptions."""

from copy import deepcopy
import json

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.private_backup import (
    PRIVATE_TRANSFER_VERSION,
    decode_document,
    encode_document,
)
from tests.test_private_backup_fidelity import private_document
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
