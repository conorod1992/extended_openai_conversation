"""Reviewed exports omit complete credential-bearing executable units."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.sharing import (
    project_configuration,
)
from tests.test_secret_restore_context import _rest_tool

NAMES = [
    "Ocp-Apim-Subscription-Key",
    "X-Auth-Key",
    "x-functions-key",
    "access_key",
    "private_key",
    "app_key",
    "license_key",
    "ACCESS-KEY",
    "privateKey",
    "App.Key",
    "LicenseKey",
]
SECRET = "fabricated-audit-credential-42"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "location", ["headers", "params", "payload", "schema", "nested"]
)
def test_reviewed_names_omit_units(name, location):
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    if location in {"headers", "params"}:
        tool["function"][location] = {name: SECRET}
    elif location == "payload":
        tool["function"]["payload"] = json.dumps({"auth": {name: SECRET}})
    elif location == "nested":
        tool["function"]["payload"] = {"auth": {name: SECRET}}
    else:
        tool["spec"]["parameters"] = {
            "type": "object",
            "properties": {name: {"type": "string", "default": SECRET}},
        }
    original = deepcopy(tool)
    public, summary = project_configuration({"functions": [tool]}, reviewed=True)
    assert public["functions"] == []
    assert SECRET not in json.dumps([public, summary])
    assert tool == original


@pytest.mark.parametrize("query", ["access%5Fkey", "X%2DAuth%2DKey", "appid"])
def test_reviewed_http_query_names(query):
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    tool["function"]["resource"] = (
        f"https://api.openweathermap.org/data/2.5/weather?{query}={SECRET}"
    )
    public, _ = project_configuration({"functions": [tool]}, reviewed=True)
    assert public["functions"] == []


@pytest.mark.parametrize(
    "name", ["key", "application_id", "appid", "access_key_name", "license_key_enabled"]
)
def test_public_schema_names_and_metadata(name):
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    tool["spec"]["parameters"] = {
        "type": "object",
        "properties": {name: {"type": "string", "default": "public-value"}},
    }
    public, _ = project_configuration({"functions": [tool]}, reviewed=True)
    assert public["functions"] == [tool]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("format", ["setup", "configuration", "rule"])
async def test_complete_reviewed_exports_and_private_fidelity(hass, name, format):
    from custom_components.extended_openai_conversation_responses import (
        backup,
        management_ui,
        transfer,
    )
    from custom_components.extended_openai_conversation_responses.request_rule_packs import (
        export_rule_pack,
    )
    from tests.test_request_rules import MemoryStore, RequestRules, local_rule
    from tests.test_transfer import _portable_document

    tool = _rest_tool()
    tool["function"]["headers"] = {}
    tool["function"]["payload"] = json.dumps({"nested": {name: SECRET}})
    config = {"functions": [tool]}
    before = deepcopy(config)
    if format == "setup":
        document = _portable_document(mode="setup")
        document["version"] = 2
        document["sections"]["configuration"] = config
        exported = transfer.finalize_setup_export(document, reviewed=True)
        assert exported["document"]["sections"]["configuration"]["functions"] == []
    elif format == "configuration":
        exported = management_ui._export_agent(
            SimpleNamespace(title="Test", data=config), reviewed=True
        )
        assert exported["config"]["functions"] == []
    else:
        rule = local_rule()
        rule["action"]["actions"] = [
            {"action": "rest_command.report", "data": {name: SECRET}}
        ]
        manager = RequestRules(MemoryStore({"rules": [rule]}))
        await manager.async_initialize()
        exported = export_rule_pack(manager, "all", reviewed=True)
        assert exported["rules"] == []
        assert SECRET in manager.snapshot()["rules"][0]["action"]["canonical_signature"]
    assert SECRET not in json.dumps(exported)
    assert config == before
    assert SECRET in json.dumps(backup.private_configuration_snapshot(config))


@pytest.mark.parametrize("name", NAMES)
def test_schema_literals_are_omitted_but_type_only_declarations_survive(name):
    tool = _rest_tool()
    tool["function"]["headers"] = {}
    schema = {"type": "object", "properties": {name: {"type": "string"}}}
    tool["spec"]["parameters"] = schema
    assert project_configuration({"functions": [tool]}, reviewed=True)[0][
        "functions"
    ] == [tool]
    schema["default"] = {name: SECRET}
    assert (
        project_configuration({"functions": [tool]}, reviewed=True)[0]["functions"]
        == []
    )


@pytest.mark.parametrize("location", ["params", "payload", "json_payload"])
def test_appid_requires_known_provider_context_and_legacy_policy_is_unchanged(location):
    from custom_components.extended_openai_conversation_responses.secret_redaction import (
        redact_secrets,
    )

    tool = _rest_tool()
    request = tool["function"]
    request["headers"] = {}
    field = "params" if location == "params" else "payload"
    request[field] = (
        json.dumps({"auth": {"aPpId": SECRET}})
        if location == "json_payload"
        else {"aPpId": SECRET}
    )
    # Generic app identifiers remain reviewed public content.
    assert project_configuration({"functions": [tool]}, reviewed=True)[0][
        "functions"
    ] == [tool]
    request["resource"] = "https://api.openweathermap.org/data/2.5/weather"
    assert (
        project_configuration({"functions": [tool]}, reviewed=True)[0]["functions"]
        == []
    )
    assert redact_secrets({"functions": [tool]}) == {"functions": [tool]}
    for name in NAMES:
        # New classifications must not change public projections used to match
        # destination-bound credentials in historical artifacts.
        assert redact_secrets({name: SECRET}) == {name: SECRET}
