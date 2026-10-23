"""Sensitive assertions cannot escape by using a non-literal schema keyword."""

from copy import deepcopy
import json
import string

from hypothesis import given, settings, strategies as st
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
from tests.test_sharing_schema_context import configuration_artifact
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


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize(
    "location",
    [
        "explicit",
        "pattern",
        "additionalProperties",
        "unevaluatedProperties",
        "composed",
        "referenced",
        "nested",
    ],
)
@pytest.mark.parametrize("assertion", ASSERTIONS)
def test_sensitive_assertions_are_omitted_from_real_artifacts(
    hass, format, persisted, location, assertion
):
    schema = schema_for(assertion, location)
    Draft202012Validator.check_schema(schema)
    config = configuration(schema, persisted)
    before = deepcopy(config)
    agent_config_snapshot(config)
    artifact, public, serialized = configuration_artifact(config, format)
    assert public["functions"] == []
    assert SECRET not in serialized
    assert "731619" not in serialized
    assert any(
        item["field"] == "function_tool_1" for item in artifact["security"]["omitted"]
    )
    agent_config_snapshot(public)
    if format == "setup":
        assert (
            yaml.safe_load(
                transfer.inspect_transfer(artifact, "new-agent").config["functions"]
            )
            == []
        )
    assert config == before


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


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("assertion", ASSERTIONS)
def test_public_property_constraints_remain_unchanged(hass, format, assertion):
    config = configuration(schema_for(assertion, public=True))
    agent_config_snapshot(config)
    artifact, public, _serialized = configuration_artifact(config, format)
    assert public["functions"] == config["functions"]
    assert not artifact["security"]["omitted"]


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize(
    "location",
    ["explicit", "additionalProperties", "unevaluatedProperties", "referenced"],
)
@pytest.mark.parametrize(
    "child",
    [
        {
            "type": ["string", "null"],
            "minLength": 1,
            "maxLength": 80,
            "format": "password",
            "description": "Account password",
            "readOnly": False,
            "writeOnly": True,
            "deprecated": False,
        },
        {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 8,
            "uniqueItems": True,
        },
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "minProperties": 1,
            "maxProperties": 8,
            "additionalProperties": False,
        },
        {
            "type": "array",
            "contains": {"type": "integer"},
            "minContains": 1,
            "maxContains": 8,
        },
    ],
)
def test_classified_sensitive_shapes_remain_portable(hass, format, location, child):
    schema = schema_for(child, location)
    Draft202012Validator.check_schema(schema)
    config = configuration(schema)
    agent_config_snapshot(config)
    artifact, public, _serialized = configuration_artifact(config, format)
    assert public["functions"] == config["functions"]
    assert not artifact["security"]["omitted"]
    imported = (
        transfer.inspect_transfer(artifact, "new-agent").config
        if format == "setup"
        else public
    )
    functions = imported["functions"]
    if isinstance(functions, str):
        functions = yaml.safe_load(functions)
    assert functions == config["functions"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("type", SECRET),
        ("type", ["string", SECRET]),
        ("minLength", SECRET),
        ("uniqueItems", SECRET),
        ("required", {"opaque": SECRET}),
        ("description", {"opaque": SECRET}),
    ],
)
def test_classified_keyword_spelling_does_not_bypass_value_shape(hass, field, value):
    config = configuration(schema_for({field: value}))
    artifact, public, serialized = configuration_artifact(config, "setup")
    assert public["functions"] == []
    assert SECRET not in serialized
    assert artifact["security"]["omitted"]


@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_integral_float_shape_counts_remain_portable(hass, format):
    # JSON Schema integer-valued counts also permit JSON's 1.0 spelling. The
    # composed form transports this valid schema through the local validator.
    schema = {
        "type": "object",
        "allOf": [
            {
                "properties": {
                    "password": {"type": "string", "minLength": 1.0, "maxLength": 80.0}
                }
            }
        ],
    }
    Draft202012Validator.check_schema(schema)
    config = configuration(schema)
    agent_config_snapshot(config)
    _artifact, public, _serialized = configuration_artifact(config, format)
    assert public["functions"] == config["functions"]


@settings(max_examples=30)
@given(
    secret=st.text(
        alphabet=string.ascii_letters + string.digits, min_size=1, max_size=50
    ),
    keyword=st.sampled_from(["pattern", "format", "x-new-assertion"]),
)
def test_unclassified_sensitive_assertions_are_value_independent(secret, keyword):
    # Keep generated custom formats outside the finite classified vocabulary.
    value = "unclassified:" + secret if keyword == "format" else secret
    for format in ("setup", "configuration"):
        artifact, public, _serialized = configuration_artifact(
            configuration(schema_for({"type": "string", keyword: value})), format
        )
        assert public["functions"] == []
        assert artifact["security"]["omitted"]


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
