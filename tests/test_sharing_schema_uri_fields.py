"""Schema identities and dialect URIs share the reference credential policy."""

from copy import deepcopy
import json

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_sharing_schema_references import tool_with_schema

SECRET = "FABRICATED-private-schema-uri-42"


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
