"""Wildcard property values have no public-name proof in reviewed sharing."""

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


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("keyword", WILDCARDS)
@pytest.mark.parametrize("literal", LITERALS)
@pytest.mark.parametrize("placement", ["direct", "nested", "composed", "referenced"])
def test_wildcard_literals_are_omitted_from_real_artifacts(
    hass, format, persisted, keyword, literal, placement
):
    schema = wildcard_schema(keyword, literal=literal, placement=placement)
    Draft202012Validator.check_schema(schema)
    config = configuration(schema, persisted)
    before = deepcopy(config)
    agent_config_snapshot(config)

    artifact, public, serialized = configuration_artifact(config, format)

    assert SECRET not in serialized
    assert public["functions"] == []
    assert any(
        item["field"] == "function_tool_1" for item in artifact["security"]["omitted"]
    )
    agent_config_snapshot(public)
    if format == "setup":
        imported = transfer.inspect_transfer(artifact, "fresh-target").config
        assert yaml.safe_load(imported["functions"]) == []
    assert config == before


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


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("keyword", WILDCARDS)
@pytest.mark.parametrize(
    "reference",
    [{"$ref": "#/missing"}, {"$dynamicRef": "#value"}, {"$recursiveRef": "#"}],
)
def test_unprovable_wildcard_references_are_omitted(hass, format, keyword, reference):
    schema = {"type": "object", keyword: reference}
    artifact, public, _serialized = configuration_artifact(
        configuration(schema), format
    )
    assert public["functions"] == []
    assert artifact["security"]["omitted"]


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("keyword", WILDCARDS)
def test_property_name_constraints_do_not_grant_wildcard_value_exemptions(
    hass, format, keyword
):
    schema = wildcard_schema(keyword)
    schema["propertyNames"] = {"const": "password"}
    _artifact, public, serialized = configuration_artifact(
        configuration(schema), format
    )
    assert public["functions"] == []
    assert SECRET not in serialized


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("keyword", WILDCARDS)
@pytest.mark.parametrize("applicator", ["allOf", "anyOf", "oneOf"])
def test_wildcard_value_sensitivity_survives_child_composition(
    hass, format, keyword, applicator
):
    schema = {
        "type": "object",
        "required": ["password"],
        # An unrelated public declaration must not change wildcard policy.
        "properties": {"units": {"type": "string", "enum": ["c", "f"]}},
        keyword: {applicator: [{"type": "string", "const": SECRET}]},
    }
    Draft202012Validator.check_schema(schema)
    config = configuration(schema)
    agent_config_snapshot(config)
    artifact, public, serialized = configuration_artifact(config, format)
    assert public["functions"] == []
    assert SECRET not in serialized
    assert artifact["security"]["omitted"]


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("keyword", WILDCARDS)
@pytest.mark.parametrize(
    "definition",
    [
        True,
        False,
        {"type": "string"},
        {"type": "object", "properties": {"password": {"type": "string"}}},
    ],
)
def test_wildcard_boolean_and_shape_schemas_remain_portable(
    hass, format, keyword, definition
):
    schema = {"type": "object", "properties": {}, keyword: definition}
    config = configuration(schema)
    agent_config_snapshot(config)
    artifact, public, _serialized = configuration_artifact(config, format)
    assert public["functions"] == config["functions"]
    assert not artifact["security"]["omitted"]
    imported = (
        transfer.inspect_transfer(artifact, "fresh-target").config
        if format == "setup"
        else public
    )
    functions = imported["functions"]
    if isinstance(functions, str):
        functions = yaml.safe_load(functions)
    assert functions[0]["spec"]["parameters"] == schema


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("keyword", WILDCARDS)
def test_wildcard_type_only_local_references_remain_portable(hass, format, keyword):
    schema = {
        "type": "object",
        keyword: {"$ref": "#/$defs/value"},
        "$defs": {"value": {"type": "string"}},
    }
    config = configuration(schema)
    _artifact, public, _serialized = configuration_artifact(config, format)
    assert public["functions"] == config["functions"]


@pytest.mark.parametrize("keyword", WILDCARDS)
def test_public_literals_use_named_fields_rather_than_wildcard_exemptions(
    hass, keyword
):
    schema = {
        "type": "object",
        "properties": {"units": {"type": "string", "enum": ["c", "f"]}},
        keyword: False,
    }
    config = configuration(schema)
    _artifact, public, _serialized = configuration_artifact(config, "setup")
    assert public["functions"] == config["functions"]
    # Even apparently public wildcard literals have no field-name proof. This
    # is an explicit conservative omission, not an entropy/name heuristic.
    schema = {"type": "object", keyword: {"type": "string", "enum": ["c", "f"]}}
    artifact, public, _serialized = configuration_artifact(
        configuration(schema), "setup"
    )
    assert public["functions"] == []
    assert artifact["security"]["omitted"]


@settings(max_examples=30)
@given(
    secret=st.text(
        alphabet=string.ascii_letters + string.digits, min_size=1, max_size=50
    )
)
def test_unfamiliar_wildcard_literals_do_not_depend_on_credential_patterns(secret):
    for keyword in WILDCARDS:
        for format in ("setup", "configuration"):
            artifact, public, _serialized = configuration_artifact(
                configuration(wildcard_schema(keyword, secret=secret)), format
            )
            assert public["functions"] == []
            assert artifact["security"]["omitted"]


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
