"""Public projection excludes opaque content independently of secret format."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.sharing import (
    SHARING_VERSION,
    project_configuration,
)
from tests.test_secret_restore_context import _rest_tool
from tests.test_transfer import _portable_document


@pytest.mark.parametrize(
    "secret",
    ["ordinary words", "1234", "z9Q!unfamiliar", "sk-FABRICATED123456789012345"],
)
@pytest.mark.parametrize("persisted", [False, True])
def test_restricted_artifact_noninterference(hass, secret, persisted):
    tool = _rest_tool()
    tool["function"].update(
        headers={"Unfamiliar-Auth": secret},
        resource="https://example.invalid/" + secret,
        payload=json.dumps({"opaque": {"passphrase": secret}}),
    )
    tool["spec"]["parameters"] = {
        "type": "object",
        "properties": {secret: {"default": secret}},
    }
    config = {
        "functions": yaml.safe_dump([tool]) if persisted else [tool],
        "prompt": secret,
        "future_field": {secret: secret},
    }
    document = _portable_document(mode="setup")
    document["version"] = SHARING_VERSION
    document["agent"]["title"] = secret
    document["sections"]["configuration"] = config
    before = deepcopy(document)
    result = transfer.finalize_setup_export(document)
    assert secret not in result["json"]
    assert secret not in result["filename"]
    assert result["document"]["sections"]["configuration"]["functions"] == []
    assert result["security"]["omitted"]
    assert document == before
    transfer.inspect_transfer(result["document"], "fresh-agent")


@pytest.mark.parametrize("field", ["headers", "payload", "schema", "binary"])
def test_reviewed_credential_units_are_omitted(hass, field):
    secret = "opaque-FABRICATED-42"
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    if field == "headers":
        tool["function"]["headers"] = {"Authorization": secret}
    elif field == "payload":
        tool["function"]["payload"] = json.dumps({"password": secret})
    elif field == "binary":
        tool["function"]["payload"] = secret.encode()
    else:
        tool["spec"]["parameters"]["properties"] = {
            "password": {"type": "string", "default": secret}
        }
    config, summary = project_configuration({"functions": [tool]}, reviewed=True)
    assert config["functions"] == []
    assert summary["omitted"]
    assert secret not in json.dumps([config, summary])


def test_reviewed_innocent_custom_tool_and_schema_are_portable(hass):
    tool = _rest_tool()
    tool["function"]["headers"] = {"Accept": "application/json"}
    tool["function"]["payload"] = '{"units":"c"}'
    config, summary = project_configuration({"functions": [tool]}, reviewed=True)
    assert config["functions"] == [tool]
    assert summary["review_required"] is True


@pytest.mark.parametrize("annotation", ["default", "examples", "enum", "const"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_schema_instance_annotations_cannot_retain_designated_credentials(
    hass, annotation, nested, format
):
    from custom_components.extended_openai_conversation_responses.agent_config import (
        agent_config_snapshot,
    )

    secret = "FABRICATED-reviewed-annotation-password-42"
    schema = {
        "type": "object",
        "properties": {"password": {"type": "string"}},
        annotation: [{"password": secret}]
        if annotation in {"examples", "enum"}
        else {"password": secret},
    }
    if nested:
        schema = {"type": "object", "properties": {"auth": schema}}
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    tool["spec"]["parameters"] = schema
    config = {"functions": [tool]}
    agent_config_snapshot(config)
    if format == "setup":
        document = _portable_document(mode="setup")
        document["version"] = SHARING_VERSION
        document["sections"]["configuration"] = config
        artifact = transfer.finalize_setup_export(document, reviewed=True)["document"]
        public = artifact["sections"]["configuration"]
    else:
        artifact = management_ui._export_agent(
            SimpleNamespace(title="Test", data=config), reviewed=True
        )
        public = artifact["config"]
    assert secret not in json.dumps(artifact)
    assert public["functions"] == []


@pytest.mark.parametrize(
    "kind",
    [
        "composed",
        "reference",
        "recursive_reference",
        "remote_reference",
        "signed_reference",
        "instance_schema_shape",
    ],
)
def test_sensitive_schema_composition_and_references_are_coherent_review_units(
    hass, kind
):
    secret = "FABRICATED-reviewed-schema-password-42"
    schema = {
        "type": "object",
        "properties": {"password": {"allOf": [{"type": "string", "default": secret}]}},
    }
    if kind in {"reference", "recursive_reference"}:
        schema = {
            "type": "object",
            "properties": {"password": {"$ref": "#/$defs/shared"}},
            "$defs": {
                "shared": {"type": "string", "default": secret}
                if kind == "reference"
                else {"$ref": "#/$defs/shared"}
            },
        }
    elif kind == "remote_reference":
        schema = {
            "type": "object",
            "properties": {"password": {"$ref": "https://example.invalid/schema"}},
        }
    elif kind == "signed_reference":
        schema = {"$ref": "https://example.invalid/schema?sig=" + secret}
    elif kind == "instance_schema_shape":
        schema = {
            "type": "object",
            "default": {"functions": [{"spec": {"parameters": {"password": secret}}}]},
        }
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    tool["spec"]["parameters"] = schema
    config, summary = project_configuration({"functions": [tool]}, reviewed=True)
    assert config["functions"] == []
    assert secret not in json.dumps([config, summary])


def test_safe_schema_annotations_and_local_references_remain_portable(hass):
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    tool["spec"]["parameters"] = {
        "type": "object",
        "properties": {
            "password": {"$ref": "#/$defs/plain"},
            "units": {"type": "string", "default": "c"},
        },
        "$defs": {"plain": {"type": "string", "minLength": 1}},
        "examples": [{"units": "c"}],
    }
    config, _summary = project_configuration({"functions": [tool]}, reviewed=True)
    assert config["functions"] == [tool]


def test_new_configuration_export_is_safe_and_importable(hass):
    source = SimpleNamespace(
        title="private opaque value",
        data={"prompt": "private opaque value", "functions": []},
    )
    document = management_ui._export_agent(source)
    assert document["version"] == SHARING_VERSION
    assert "private opaque value" not in json.dumps(document)
    assert (
        management_ui._parse_import_document(document)["config"]["functions"] == "[]\n"
    )


def test_safe_behavior_values_survive_without_approving_arbitrary_identifiers():
    config, _summary = project_configuration(
        {
            "temperature": 0.5,
            "archive_enabled": True,
            "chat_model": "opaque credential",
            "future_field": 1234,
        }
    )
    assert config["temperature"] == 0.5
    assert config["archive_enabled"] is True
    assert "chat_model" not in config
    assert "future_field" not in config


def test_repeated_schema_references_have_bounded_review_work(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import sharing

    tool = _rest_tool()
    tool["function"]["headers"] = {}
    definitions = {"level10": {"type": "string"}}
    for level in reversed(range(10)):
        definitions[f"level{level}"] = {
            "allOf": [{"$ref": f"#/$defs/level{level + 1}"}] * 4
        }
    tool["spec"]["parameters"] = {
        "type": "object",
        "properties": {"password": {"$ref": "#/$defs/level0"}},
        "$defs": definitions,
    }
    original = sharing._schema_annotations
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        assert calls < 1000
        return original(*args, **kwargs)

    monkeypatch.setattr(sharing, "_schema_annotations", counted)
    config, _summary = sharing.project_configuration(
        {"functions": [tool]}, reviewed=True
    )
    assert config["functions"] == [tool]
