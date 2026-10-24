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
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    export_rule_pack,
    validate_rule_pack,
)
from custom_components.extended_openai_conversation_responses.sharing import (
    SHARING_VERSION,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_request_rules import MemoryStore, RequestRules, local_rule
from tests.test_secret_restore_context import _rest_tool
from tests.test_transfer import _portable_document

SECRET = "FABRICATED-unfamiliar-instance-credential-42"


def configuration_artifact(config, format):
    """Use the production sharing writers and their actual JSON transport."""
    if format == "configuration":
        artifact = management_ui._export_agent(
            SimpleNamespace(title="Test", data=config), reviewed=True
        )
        serialized = json.dumps(artifact)
        public = artifact["config"]
    else:
        document = _portable_document(mode="setup")
        document["version"] = SHARING_VERSION
        document["sections"]["configuration"] = config
        result = transfer.finalize_setup_export(document, reviewed=True)
        serialized = result["json"]
        artifact = json.loads(serialized)
        public = artifact["sections"]["configuration"]
    return artifact, public, serialized


def instance_payload(shape, credential=True):
    instance = {"password": SECRET} if credential else {"units": "c"}
    if shape == "schema_shaped":
        return {"functions": [{"spec": {"parameters": instance}}]}
    return instance


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("shape", ["plain", "schema_shaped"])
def test_script_instance_credentials_are_excluded_from_real_artifacts(
    hass, format, persisted, shape
):
    tool = _rest_tool()
    tool["function"] = {
        "type": "script",
        "sequence": [{"variables": {"request": instance_payload(shape)}}],
    }
    config = {"functions": yaml.safe_dump([tool]) if persisted else [tool]}
    before = deepcopy(config)
    agent_config_snapshot(config)  # A valid saved Function, not quarantined data.

    artifact, public, serialized = configuration_artifact(config, format)

    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]
    assert config == before


@pytest.mark.parametrize("selection", ["all", "selected", "group"])
@pytest.mark.parametrize("shape", ["plain", "schema_shaped"])
async def test_rule_instance_credentials_cannot_become_schemas(hass, selection, shape):
    rule = local_rule()
    rule["action"]["actions"] = [
        {"action": "rest_command.report", "data": instance_payload(shape)}
    ]
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    before = manager.snapshot()

    pack = export_rule_pack(manager, selection, rule_ids=[rule["id"]], reviewed=True)

    assert SECRET not in json.dumps(pack)
    assert pack["rules"] == []
    assert pack["security"]["omitted"]
    assert validate_rule_pack(pack)["rules"] == []
    assert manager.snapshot() == before


@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_benign_schema_shaped_data_and_actual_schema_stay_portable(hass, format):
    tool = _rest_tool()
    tool["spec"]["parameters"] = {
        "type": "object",
        "properties": {"password": {"type": "string"}},
    }
    tool["function"] = {
        "type": "script",
        "sequence": [
            {"variables": {"request": instance_payload("schema_shaped", False)}}
        ],
    }
    artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, format
    )

    assert public["functions"] == [tool]
    if format == "setup":
        transfer.inspect_transfer(artifact, "new-agent")
    else:
        management_ui._parse_import_document(artifact)


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
