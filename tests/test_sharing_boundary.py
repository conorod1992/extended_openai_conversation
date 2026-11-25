"""Public artifacts obey value policy independently of structural position."""

from copy import deepcopy
import json

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    transfer,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_sharing_schema_references import tool_with_schema

SECRET = "FABRICATED-boundary-credential-42"
URLS = [
    f"https://example.invalid/docs?access_token={SECRET}",
    f"https://example.invalid/docs#access_token={SECRET}",
    f"https://user:{SECRET}@example.invalid/docs",
    f"https://example.invalid/docs?%73ig={SECRET}",
]


@pytest.mark.parametrize("uri", URLS)
def test_private_schema_urls_remain_exact(hass, uri):
    document = private_document()
    document["agent"]["config"]["functions"] = yaml.safe_dump(
        [tool_with_schema({"type": "object", "properties": {}, "description": uri})]
    )
    before = deepcopy(document)
    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])
    assert decode_document(artifact) == before
    assert (
        transfer.inspect_transfer(artifact, "fresh-agent").config
        == before["agent"]["config"]
    )
