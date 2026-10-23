"""SigV4 credentials are private in every portable HTTP configuration export."""

from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    _export_agent,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    redact_secrets,
)
from tests.test_http_parameter_template_redaction import export
from tests.test_secret_restore_context import _rest_tool

SIGNATURE = "eoai-example-presigned-signature-0123456789abcdef"
KEY_ID = "AKIAEOAIEXAMPLEKEY1234"
CREDENTIAL = KEY_ID + "/20261023/eu-west-1/s3/aws4_request"


def configuration(location, persisted=False):
    tool = _rest_tool()
    function = tool["function"]
    function["headers"] = {}
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Date": "20261023T120000Z",
        "X-Amz-Expires": "900",
        "X-Amz-SignedHeaders": "host",
        "X-Amz-Credential": CREDENTIAL,
        "X-Amz-Signature": SIGNATURE,
    }
    function["resource"] = (
        "https://bucket.s3.eu-west-1.amazonaws.com/private/object.txt"
    )
    if location == "url":
        function["resource"] += "?" + urlencode(params)
    else:
        function["params"] = params
    assert "Security-Token" not in json.dumps(function)
    tools = [tool]
    return {
        "chat_model": "gpt-4o",
        "functions": yaml.safe_dump(tools) if persisted else tools,
    }


@pytest.mark.parametrize("exporter", ["agent", "setup", "legacy"])
@pytest.mark.parametrize("location", ["url", "params"])
@pytest.mark.parametrize("persisted", [False, True])
def test_sigv4_credentials_absent_from_all_serialized_exports(
    hass, exporter, location, persisted
):
    config = configuration(location, persisted)
    agent_config_snapshot(config)  # Actual REST/schema acceptance.
    original = deepcopy(config)
    if exporter == "agent":
        document = _export_agent(SimpleNamespace(title="Signed source", data=config))
        serialized = json.dumps(document)
    else:
        result, _ = export(config, exporter)
        serialized = result["json"]
    assert SIGNATURE not in serialized
    assert KEY_ID not in serialized
    assert "bucket.s3.eu-west-1.amazonaws.com/private/object.txt" in serialized
    assert "AWS4-HMAC-SHA256" in serialized
    assert config == original


@pytest.mark.parametrize("location", ["url", "params"])
@pytest.mark.parametrize("change", [None, "host", "path", "method", "expiry"])
def test_sigv4_recovery_keeps_public_destination_binding(location, change):
    config = configuration(location)
    public = redact_secrets(config)
    assert SIGNATURE not in json.dumps(public) and KEY_ID not in json.dumps(public)
    destination = deepcopy(config)
    function = destination["functions"][0]["function"]
    if change == "host":
        function["resource"] = function["resource"].replace("bucket.s3", "other.s3")
    elif change == "path":
        function["resource"] = function["resource"].replace("/private/", "/public/")
    elif change == "method":
        function["method"] = "POST"
    elif change == "expiry":
        if location == "url":
            function["resource"] = function["resource"].replace(
                "Expires=900", "Expires=3600"
            )
        else:
            function["params"]["X-Amz-Expires"] = "3600"
    restored, kept, missing = transfer._restore_section_secrets(public, destination)
    if change is None:
        assert kept and not missing
        assert SIGNATURE in json.dumps(restored) and KEY_ID in json.dumps(restored)
    else:
        assert missing and not kept
        assert SIGNATURE not in json.dumps(restored) and KEY_ID not in json.dumps(
            restored
        )


@pytest.mark.parametrize("location", ["url", "params"])
async def test_private_sigv4_snapshots_are_unchanged(hass, location):
    config = configuration(location)
    original = deepcopy(config)
    private = backup.private_configuration_snapshot(config)
    serialized = json.dumps(yaml.safe_load(private["functions"]))
    assert SIGNATURE in serialized and KEY_ID in serialized
    assert config == original


@pytest.mark.parametrize("exporter", ["agent", "setup", "legacy"])
def test_aws_field_names_in_script_data_and_schema_are_not_http_credentials(
    hass, exporter
):
    tool = {
        "spec": {
            "name": "labels",
            "description": "Public labels",
            "parameters": {
                "type": "object",
                "properties": {
                    "X-Amz-Signature": {
                        "type": "string",
                        "description": "A public schema field",
                    }
                },
            },
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "variables": {
                        "X-Amz-Credential": "public-label",
                        "X-Amz-Signature": "public-label",
                    }
                }
            ],
        },
    }
    config = {"chat_model": "gpt-4o", "functions": [tool]}
    if exporter == "agent":
        public = _export_agent(SimpleNamespace(title="Public labels", data=config))[
            "config"
        ]
    else:
        _, public = export(config, exporter)
    assert public["functions"][0] == tool


@pytest.mark.parametrize(
    "name",
    [
        "X-Amz-Signature",
        "x-amz-signature",
        "%58-Amz-Signature",
        "X-Amz-Credential",
        "x-amz-credential",
        "%58-Amz-Credential",
    ],
)
def test_sigv4_names_are_case_insensitive_and_url_decoded_only_in_http_context(name):
    query = redact_secrets({"resource": f"https://example.test/?{name}={SIGNATURE}"})
    params = redact_secrets(
        {
            "type": "rest",
            "resource": "https://example.test/",
            "params": {name: SIGNATURE},
        }
    )
    assert SIGNATURE not in json.dumps(query)
    assert SIGNATURE not in json.dumps(params)
    assert (
        redact_secrets(
            {"type": "script", "sequence": [{"variables": {name: SIGNATURE}}]}
        )["sequence"][0]["variables"][name]
        == SIGNATURE
    )
