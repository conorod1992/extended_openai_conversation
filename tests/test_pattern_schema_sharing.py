"""Pattern-applied instance literals cannot evade credential classification."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
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

SECRET = "FABRICATED-pattern-property-credential-3951"
PATTERNS = ["^pass(?:word)$", "^p[a]ssword$"]


def config(pattern, persisted, secret=SECRET):
    schema = {
        "type": "object",
        "patternProperties": {pattern: {"type": "string", "const": secret}},
    }
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"password": secret})
    assert not validator.is_valid({"password": "wrong"})
    tools = [tool_with_schema(schema)]
    return {"functions": yaml.safe_dump(tools) if persisted else tools}


@pytest.mark.parametrize("persisted", [False, True])
def test_private_artifact_control(hass, persisted):
    document = private_document()
    document["agent"]["config"].update(config(PATTERNS[0], persisted))
    before = deepcopy(document)
    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])
    assert decode_document(artifact) == before
    assert (
        transfer.inspect_transfer(artifact, "fresh-agent").config
        == before["agent"]["config"]
    )
    assert document == before
