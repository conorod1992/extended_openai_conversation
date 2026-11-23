"""Schema-shaped instance data cannot acquire configuration schema privileges."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from custom_components.extended_openai_conversation_responses.sharing import (
    SHARING_VERSION,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_secret_restore_context import _rest_tool
from tests.test_transfer import _portable_document

SECRET = "FABRICATED-unfamiliar-instance-credential-42"


def configuration_artifact(config, format):
    """Use the production sharing writers and their actual JSON transport."""
    if format == "configuration":
        artifact = management_ui._export_agent(
            SimpleNamespace(title="Test", data=config)
        )
        serialized = json.dumps(artifact)
        public = artifact["config"]
    else:
        document = _portable_document(mode="setup")
        document["version"] = SHARING_VERSION
        document["sections"]["configuration"] = config
        result = transfer.finalize_setup_export(document)
        serialized = result["json"]
        artifact = json.loads(serialized)
        public = artifact["sections"]["configuration"]
    return artifact, public, serialized


def rule_setup_artifact(rules):
    """Project and serialize rules through the production Shareable Setup writer."""
    document = _portable_document(mode="setup")
    document["version"] = SHARING_VERSION
    document["sections"]["request_rules"] = rules
    return transfer.finalize_setup_export(document)


def instance_payload(shape, credential=True):
    instance = {"password": SECRET} if credential else {"units": "c"}
    if shape == "schema_shaped":
        return {"functions": [{"spec": {"parameters": instance}}]}
    return instance


@pytest.mark.parametrize("shape", ["plain", "schema_shaped"])
def test_private_backup_keeps_the_same_instance_credentials_exactly(hass, shape):
    document = private_document()
    tool = _rest_tool()
    tool["function"] = {
        "type": "script",
        "sequence": [{"variables": {"request": instance_payload(shape)}}],
    }
    document["agent"]["config"]["functions"] = yaml.safe_dump([tool])
    before = deepcopy(document)

    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])

    assert decode_document(artifact) == before
    prepared = transfer.inspect_transfer(artifact, "new-agent")
    assert prepared.config == before["agent"]["config"]
    assert prepared.provider_connection == before["provider_connection"]
    assert document == before
