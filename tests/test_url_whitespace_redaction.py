"""REST URL spelling cannot bypass complete export redaction or destination checks."""

from copy import deepcopy
import json

import pytest
import yaml
from yarl import URL

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    redact_secrets,
)
from tests.test_backup import _document
from tests.test_secret_restore_context import _rest_tool
from tests.test_transfer import _portable_document

SECRET = "EOAI_URL_WHITESPACE_CREDENTIAL"
SPELLINGS = ["{}", " {}", "\n{}", "https:\n//{}", "\thttps:\r\n\t//{}"]


def config_for(spelling, persisted, kind):
    tool = _rest_tool()
    url = (
        f"https://apiuser:{SECRET}@example.test/data?units=c"
        if kind == "userinfo"
        else f"https://example.test/data?units=c&api_key={SECRET}"
    )
    resource = spelling.format(
        url.removeprefix("https://") if "//{}" in spelling else url
    )
    assert str(URL(resource)) == url
    tool["function"]["resource"] = resource
    tool["function"]["headers"] = {}
    config = {
        "chat_model": "gpt-4o",
        "functions": yaml.safe_dump([tool]) if persisted else [tool],
    }
    return agent_config_snapshot(config)


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("kind", ["query", "userinfo"])
@pytest.mark.parametrize("exporter", ["setup", "backup"])
def test_complete_exports_redact_client_normalized_urls(
    hass, spelling, persisted, kind, exporter
):
    config = config_for(spelling, persisted, kind)
    original = deepcopy(config)
    if exporter == "setup":
        document = _portable_document(mode="setup")
        document["sections"]["configuration"] = config
        result = transfer.finalize_setup_export(document)
    else:
        document = _document()
        document["agent"]["config"] = config
        result = backup.finalize_backup_snapshot(document)
    assert SECRET not in result["json"]
    inspected = transfer.inspect_transfer(result["document"], "target")
    assert any("resource" in path for path in inspected.redacted_sensitive_fields)
    assert SECRET not in json.dumps(inspected.raw_configuration)
    assert config == original


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("change", [None, "host", "path", "method", "query"])
def test_normalized_url_recovery_remains_destination_bound(hass, spelling, change):
    config = config_for(spelling, False, "query")
    public = redact_secrets(config)
    destination = deepcopy(config)
    tools = (
        yaml.safe_load(destination["functions"])
        if isinstance(destination["functions"], str)
        else destination["functions"]
    )
    function = tools[0]["function"]
    if change == "method":
        function["method"] = "POST"
    elif change == "host":
        function["resource"] = function["resource"].replace(
            "example.test", "other.test"
        )
    elif change == "path":
        function["resource"] = function["resource"].replace("/data", "/changed")
    elif change == "query":
        function["resource"] += "&extra=1"
    destination["functions"] = tools
    restored, kept, missing = transfer._restore_section_secrets(public, destination)
    if change is None:
        assert kept and not missing
        assert SECRET in json.dumps(restored)
    else:
        assert missing and not kept
        assert SECRET not in json.dumps(restored)


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_resource_templates_keep_conservative_recovery_policy(spelling):
    url = "https://example.test/data?units=c"
    resource = spelling.format(
        url.removeprefix("https://") if "//{}" in spelling else url
    )
    config = {"resource_template": resource}
    assert redact_secrets(config) == (
        config
        if spelling == "{}"
        else {"resource_template": {"__extended_openai_redacted_url__": None}}
    )
