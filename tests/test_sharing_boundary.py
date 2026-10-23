"""Public artifacts obey value policy independently of structural position."""

from copy import deepcopy
import json

from hypothesis import given, settings, strategies as st
from jsonschema import Draft202012Validator
import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
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
