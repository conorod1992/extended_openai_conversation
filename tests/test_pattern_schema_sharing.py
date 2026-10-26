"""Pattern-applied instance literals cannot evade credential classification."""

from copy import deepcopy
import json
from types import SimpleNamespace

from jsonschema import Draft202012Validator
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
from custom_components.extended_openai_conversation_responses.sharing import (
    SHARING_VERSION,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_sharing_schema_context import configuration_artifact
from tests.test_sharing_schema_references import tool_with_schema
from tests.test_transfer import _portable_document

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


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("pattern", PATTERNS)
def test_pattern_credentials_are_omitted_from_final_artifacts(
    hass, format, persisted, pattern
):
    source = config(pattern, persisted)
    before = deepcopy(source)
    agent_config_snapshot(source)
    artifact, public, serialized = configuration_artifact(source, format)
    assert SECRET not in serialized
    assert public["functions"] == []
    assert any(x["field"] == "function_tool_1" for x in artifact["security"]["omitted"])
    assert source == before


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
def test_equivalent_regex_control(hass, format, persisted):
    source = config("^(?:password)$", persisted)
    artifact, public, serialized = configuration_artifact(source, format)
    assert SECRET not in serialized
    assert not public["functions"]
    assert artifact["security"]["omitted"]


@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_restricted_control(hass, format):
    source = config(PATTERNS[0], False)
    if format == "configuration":
        artifact = management_ui._export_agent(
            SimpleNamespace(title="Test", data=source)
        )
    else:
        document = _portable_document(mode="setup")
        document["version"] = SHARING_VERSION
        document["sections"]["configuration"] = source
        artifact = transfer.finalize_setup_export(document)["document"]
    assert SECRET not in json.dumps(artifact)


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


@pytest.mark.parametrize("pattern", ["^pass(?:word)$", "^p[a]ssword$", ".*", "(a+)+$"])
def test_ambiguous_shape_only_patterns_remain_portable(hass, pattern):
    tool = tool_with_schema(
        {"type": "object", "patternProperties": {pattern: {"type": "string"}}}
    )
    _artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, "setup"
    )
    assert public["functions"] == [tool]


@pytest.mark.parametrize("pattern", ["^units$", "^display_name$", "^unit-code$"])
def test_provable_literal_public_patterns_remain_portable(hass, pattern):
    tool = tool_with_schema(
        {
            "type": "object",
            "patternProperties": {pattern: {"type": "string", "enum": ["c", "f"]}},
        }
    )
    _artifact, public, _serialized = configuration_artifact(
        {"functions": [tool]}, "setup"
    )
    assert public["functions"] == [tool]


@pytest.mark.parametrize(
    "annotation", ["default", "example", "examples", "enum", "const"]
)
def test_unclassified_pattern_instance_literals_are_omitted(hass, annotation):
    tool = tool_with_schema(
        {
            "type": "object",
            "patternProperties": {
                ".*": {
                    "type": "string",
                    annotation: [SECRET]
                    if annotation in {"examples", "enum"}
                    else SECRET,
                }
            },
        }
    )
    artifact, public, serialized = configuration_artifact(
        {"functions": [tool]}, "setup"
    )
    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]
