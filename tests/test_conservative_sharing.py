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
