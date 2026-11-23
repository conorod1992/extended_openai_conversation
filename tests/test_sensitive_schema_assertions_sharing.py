"""Sensitive assertions cannot escape by using a non-literal schema keyword."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

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
from tests.test_wildcard_schema_sharing import configuration

SECRET = "FABRICATEDOpaqueAssertionCredential91"
ASSERTIONS = [
    {"type": "string", "pattern": "^" + SECRET + "$"},
    {"type": "integer", "minimum": 731619, "maximum": 731619},
    {"type": "integer", "exclusiveMinimum": 731618, "exclusiveMaximum": 731620},
    {"type": "integer", "multipleOf": 731619},
    {"type": "string", "format": SECRET},
    {"type": "string", "x-future-assertion": {"allowed": [SECRET]}},
]


def schema_for(child, location="explicit", *, public=False):
    name = "units" if public else "password"
    schema = {"type": "object", "required": [name]}
    if location == "explicit":
        schema["properties"] = {name: deepcopy(child)}
    elif location == "pattern":
        schema["patternProperties"] = {"^" + name + "$": deepcopy(child)}
    elif location in {"additionalProperties", "unevaluatedProperties"}:
        schema[location] = deepcopy(child)
    elif location == "composed":
        schema["allOf"] = [{"properties": {name: deepcopy(child)}}]
    elif location == "referenced":
        schema["properties"] = {name: {"$ref": "#/$defs/value"}}
        schema["$defs"] = {"value": deepcopy(child)}
    elif location == "nested":
        schema["properties"] = {
            "container": {"type": "object", "properties": {name: deepcopy(child)}}
        }
        schema["required"] = ["container"]
    return schema


@pytest.mark.parametrize("location", ["explicit", "additionalProperties"])
@pytest.mark.parametrize("assertion", ASSERTIONS[:3])
def test_credential_constraints_are_valid_and_enforced(hass, location, assertion):
    schema = schema_for(assertion, location)
    secret = SECRET if assertion["type"] == "string" else 731619
    wrong = "wrong" if isinstance(secret, str) else secret + 1
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"password": secret})
    assert not validator.is_valid({"password": wrong})
    config = configuration(schema)
    agent_config_snapshot(config)
    spec = config["functions"][0]["spec"]
    assert validate_function_arguments(spec, {"password": secret}) == {
        "password": secret
    }
    with pytest.raises(HomeAssistantError):
        validate_function_arguments(spec, {"password": wrong})


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("phase", ["committed", "applying"])
@pytest.mark.parametrize("persisted", [False, True])
async def test_private_assertions_restore_and_recover_exactly(
    monkeypatch, hass, custom, phase, persisted
):
    tx = RestoreHarness(monkeypatch)
    before = deepcopy(tx.subentry.data)
    document = private_document()
    child = {
        "type": "string",
        "pattern": "^" + SECRET + "\x85[redacted]$",
        "x-assertions": deepcopy(ASSERTIONS),
    }
    incoming = configuration(schema_for(child, "referenced"), persisted)
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
    assert transfer.inspect_transfer(artifact, "new-agent").config == incoming
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
