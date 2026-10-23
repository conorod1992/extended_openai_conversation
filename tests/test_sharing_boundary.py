"""Public artifacts obey value policy independently of structural position."""

from copy import deepcopy
import json
import string

from hypothesis import given, settings, strategies as st
from jsonschema import Draft202012Validator
import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    sharing,
    transfer,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from custom_components.extended_openai_conversation_responses.sharing import (
    SHARING_VERSION,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_sharing_schema_context import configuration_artifact
from tests.test_sharing_schema_references import tool_with_schema
from tests.test_transfer import _portable_document

SECRET = "FABRICATED-boundary-credential-42"
URLS = [
    f"https://example.invalid/docs?access_token={SECRET}",
    f"https://example.invalid/docs#access_token={SECRET}",
    f"https://user:{SECRET}@example.invalid/docs",
    f"https://example.invalid/docs?%73ig={SECRET}",
]


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("keyword", ["description", "title", "$comment"])
@pytest.mark.parametrize("uri", URLS)
def test_recognised_urls_cannot_acquire_schema_exemptions(
    hass, format, persisted, keyword, uri
):
    schema = {
        "type": "object",
        "properties": {"units": {"type": "string", keyword: uri}},
    }
    Draft202012Validator.check_schema(schema)
    tool = tool_with_schema(schema)
    config = {"functions": yaml.safe_dump([tool]) if persisted else [tool]}
    before = deepcopy(config)
    artifact, public, serialized = configuration_artifact(config, format)
    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]
    assert config == before


@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_public_schema_structure_annotations_and_templates_stay_portable(hass, format):
    schema = {
        "type": "object",
        "properties": {
            "password": {"type": "string"},
            "units": {
                "type": "string",
                "description": "https://example.invalid/docs?units=c",
                "enum": ["c", "f"],
            },
        },
        "required": ["password"],
        "additionalProperties": False,
    }
    tool = tool_with_schema(schema)
    tool["function"]["resource"] = "https://weather.example.test/api"
    artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == [tool]
    assert all(
        item["field"] != "function_tool_1" for item in artifact["security"]["omitted"]
    )


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


@settings(max_examples=35, deadline=None)
@given(st.text(min_size=1, max_size=40))
def test_unclassified_envelope_fields_never_enter_setup_artifact(fabricated):
    document = _portable_document(mode="setup")
    document["version"] = SHARING_VERSION
    document["future_private_field"] = {"arbitrary": fabricated}
    artifact = transfer.finalize_setup_export(document)["document"]
    assert "future_private_field" not in artifact
    assert set(artifact) == {
        "format",
        "version",
        "mode",
        "created_at",
        "integration_version",
        "agent",
        "sections",
        "security",
    }


@settings(max_examples=25, deadline=None)
@given(
    st.text(
        alphabet=string.ascii_letters + string.digits + "_-.~", min_size=4, max_size=35
    )
)
def test_unfamiliar_credential_values_do_not_depend_on_known_token_formats(fabricated):
    # This proves designated-field exclusions over these generated structures,
    # not that arbitrary strings in reviewed content can be recognised as secret.
    for position in ("header", "schema_default", "script_data"):
        tool = tool_with_schema({"type": "object", "properties": {}})
        if position == "header":
            tool["function"]["headers"]["Authorization"] = fabricated
        elif position == "schema_default":
            tool["spec"]["parameters"]["properties"]["password"] = {
                "type": "string",
                "default": fabricated,
            }
        else:
            tool["function"] = {
                "type": "script",
                "sequence": [{"variables": {"password": fabricated}}],
            }
        config = {"functions": [tool]}
        for format in ("setup", "configuration"):
            _artifact, public, _serialized = configuration_artifact(config, format)
            assert public["functions"] == []


@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_final_unit_admission_detects_regressed_schema_walker(
    hass, monkeypatch, format
):
    tool = tool_with_schema(
        {"type": "object", "properties": {}, "description": URLS[0]}
    )
    # Simulate a future schema walk accidentally returning unchecked annotations.
    monkeypatch.setattr(
        sharing, "_schema_annotations", lambda value, root, **kwargs: value
    )
    _artifact, public, serialized = configuration_artifact(
        {"functions": [tool]}, format
    )
    assert public["functions"] == []
    assert SECRET not in serialized


@settings(max_examples=25, deadline=None)
@given(st.text(max_size=50))
def test_restricted_artifacts_do_not_expand_with_unclassified_custom_content(
    fabricated,
):
    document = _portable_document(mode="setup")
    document["version"] = SHARING_VERSION
    document["sections"]["configuration"].update(
        {
            "future_private_field": {"password": fabricated},
            "prompt": fabricated,
        }
    )
    document["sections"]["future_section"] = {"arbitrary": fabricated}
    artifact = transfer.finalize_setup_export(document)["document"]
    public = artifact["sections"]["configuration"]
    assert "prompt" not in public and "future_private_field" not in public
    assert "future_section" not in artifact["sections"]
    assert any(
        item["field"] == "unclassified_section"
        for item in artifact["security"]["omitted"]
    )
