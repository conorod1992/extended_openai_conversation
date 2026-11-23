"""Wildcard property values have no public-name proof in reviewed sharing."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    PRIVATE_TRANSFER_VERSION,
    decode_document,
    encode_document,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_private_backup_fidelity import private_document
from tests.test_selective_restore_concurrency import RestoreHarness
from tests.test_sharing_schema_references import tool_with_schema

SECRET = "FABRICATED-wildcard-credential-91"
WILDCARDS = ["additionalProperties", "unevaluatedProperties"]
LITERALS = ["const", "enum", "default", "example", "examples"]


def wildcard_schema(keyword, *, literal="const", secret=SECRET, placement="direct"):
    value = [secret] if literal in {"enum", "examples"} else secret
    child = {"type": "string", literal: value}
    schema = {"type": "object", "required": ["password"], keyword: child}
    if placement == "nested":
        schema = {
            "type": "object",
            "properties": {"container": schema},
            "required": ["container"],
        }
    elif placement == "composed":
        schema = {
            "type": "object",
            "allOf": [{"required": ["password"]}, {keyword: child}],
        }
    elif placement == "referenced":
        schema[keyword] = {"$ref": "#/$defs/value"}
        schema["$defs"] = {"value": child}
    return schema


def configuration(schema, persisted=False):
    tool = tool_with_schema(schema)
    tool["function"]["method"] = "POST"
    tool["function"]["payload_template"] = "{{ password }}"
    return {"functions": yaml.safe_dump([tool]) if persisted else [tool]}


@pytest.mark.parametrize("keyword", WILDCARDS)
def test_wildcard_credential_schema_is_valid_and_enforced(hass, keyword):
    schema = wildcard_schema(keyword)
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"password": SECRET})
    assert not validator.is_valid({"password": "wrong"})
    config = configuration(schema)
    agent_config_snapshot(config)
    # The local validator supports additionalProperties; newer applicators are
    # transported as authored rather than silently rewritten on export/import.
    if keyword == "additionalProperties":
        spec = config["functions"][0]["spec"]
        assert validate_function_arguments(spec, {"password": SECRET}) == {
            "password": SECRET
        }
        with pytest.raises(HomeAssistantError):
            validate_function_arguments(spec, {"password": "wrong"})


@pytest.mark.parametrize("keyword", WILDCARDS)
@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("phase", ["committed", "applying"])
async def test_private_wildcard_schema_restore_and_recovery_remain_exact(
    monkeypatch, hass, keyword, custom, phase
):
    tx = RestoreHarness(monkeypatch)
    before = deepcopy(tx.subentry.data)
    document = private_document()
    incoming = configuration(
        wildcard_schema(keyword, secret=SECRET + "\x85[redacted]"), True
    )
    document["agent"]["config"] = incoming
    if custom:
        document = {
            "format": transfer.TRANSFER_FORMAT,
            "version": PRIVATE_TRANSFER_VERSION,
            "mode": "custom",
            "agent": {
                key: value
                for key, value in document["agent"].items()
                if key != "config"
            },
            "created_at": document["created_at"],
            "integration_version": document["integration_version"],
            "sections": {"configuration": incoming},
            "provider_connection": document["provider_connection"],
        }
        artifact = json.loads(json.dumps(encode_document(document)))
    else:
        artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])
    assert decode_document(artifact) == document
    assert transfer.inspect_transfer(artifact, "fresh-target").config == incoming
    await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        artifact,
        sections=[transfer.SECTION_CONFIGURATION],
    )
    assert tx.subentry.data == incoming
    journal = json.loads(json.dumps(tx.journal.saved[-1]))
    journal["phase"] = phase
    tx.journal.data = journal
    await restore_recovery.async_recover_pending_restore(tx.hass, tx.entry, tx.subentry)
    assert tx.subentry.data == (incoming if phase == "committed" else before)
