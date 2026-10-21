"""Complete exports protect HTTP query representations without widening payload redaction."""

from copy import deepcopy
import json

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL,
    redact_secrets,
)
from tests.test_backup import _document
from tests.test_secret_restore_context import _rest_tool
from tests.test_transfer import _portable_document

SECRET = "EOAI_REVIEW_CREDENTIAL_42"
ALIASES = ("key", "sig", "signature", "credential")
TEMPLATES = {
    "jinja-query": "{% set base = 'https://weather.example.test' %}{{ base }}/api?api_key="
    + SECRET,
    "jinja-userinfo": "{% set base = 'https://user:"
    + SECRET
    + "@weather.example.test' %}{{ base }}/api",
    "https-jinja-query": "https://{{ host }}/api?api_key=" + SECRET,
    "https-jinja-path": "https://weather.example.test/{{ '" + SECRET + "' }}/api",
}


def configuration(kind, persisted=False, function_type="rest"):
    tool = _rest_tool()
    request = tool["function"]
    request["type"] = function_type
    if function_type == "scrape":
        request["sensor"] = [{"name": "headline", "select": "h1"}]
    if kind.startswith("params-"):
        request["params"] = {kind.removeprefix("params-"): SECRET, "units": "c"}
    else:
        del request["resource"]
        request["resource_template"] = TEMPLATES[kind]
    tools = [tool]
    return {
        "chat_model": "gpt-4o",
        "functions": yaml.safe_dump(tools) if persisted else tools,
    }


def export(config, exporter):
    if exporter == "setup":
        document = _portable_document(mode="setup")
        document["sections"]["configuration"] = config
        result = transfer.finalize_setup_export(document)
        public = result["document"]["sections"]["configuration"]
    else:
        document = _document()
        document["agent"]["config"] = config
        result = backup.finalize_backup_snapshot(document)
        public = result["document"]["agent"]["config"]
    return result, public


@pytest.mark.parametrize("kind", [*("params-" + key for key in ALIASES), *TEMPLATES])
@pytest.mark.parametrize("function_type", ["rest", "scrape"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("exporter", ["setup", "legacy"])
def test_complete_exports_remove_http_credentials(
    kind, function_type, persisted, exporter
):
    config = configuration(kind, persisted, function_type)
    original = deepcopy(config)
    result, public = export(config, exporter)
    assert SECRET not in result["json"]
    assert SECRET not in json.dumps(result["document"])
    assert redact_secrets(public) == public
    request = public["functions"][0]["function"]
    if kind.startswith("params-"):
        assert (
            request["params"][kind.removeprefix("params-")] == REDACTED_SECRET_SENTINEL
        )
        assert request["params"]["units"] == "c"
    else:
        assert request["resource_template"] == {
            "__extended_openai_redacted_url__": None
        }
    assert config == original


@pytest.mark.parametrize("key", ALIASES)
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("change", ["none", "host", "path", "method", "public-param"])
def test_params_recover_only_with_unchanged_public_destination(key, persisted, change):
    config = configuration("params-" + key, persisted)
    public = redact_secrets(config)
    request = public["functions"][0]["function"]
    assert request["params"][key] == REDACTED_SECRET_SENTINEL
    if change == "host":
        request["resource"] = "https://other.example.test/api"
    elif change == "path":
        request["resource"] += "/changed"
    elif change == "method":
        request["method"] = "POST"
    elif change == "public-param":
        request["params"]["units"] = "f"
    restored, kept, missing = transfer._restore_section_secrets(public, config)
    if change == "none":
        assert kept and not missing
        assert restored["functions"][0]["function"]["params"][key] == SECRET
    else:
        assert missing and not kept
        assert SECRET not in json.dumps(restored)


@pytest.mark.parametrize("kind", TEMPLATES)
@pytest.mark.parametrize("persisted", [False, True])
def test_ambiguous_templates_require_explicit_reentry_even_for_same_local_source(
    kind, persisted
):
    config = configuration(kind, persisted)
    public = redact_secrets(config)
    assert public["functions"][0]["function"]["resource_template"] == {
        "__extended_openai_redacted_url__": None
    }
    restored, kept, missing = transfer._restore_section_secrets(public, config)
    assert missing and not kept
    assert SECRET not in json.dumps(restored)
    assert "resource_template" not in restored["functions"][0]["function"]


@pytest.mark.parametrize("kind", [*("params-" + key for key in ALIASES), *TEMPLATES])
@pytest.mark.parametrize("persisted", [False, True])
async def test_private_snapshots_keep_credentials_without_rendering_templates(
    hass, monkeypatch, kind, persisted
):
    from homeassistant.helpers.template import Template

    def never_render(*_args, **_kwargs):
        raise AssertionError("Export/recovery snapshots must not execute templates")

    monkeypatch.setattr(Template, "async_render", never_render)
    config = configuration(kind, persisted)
    original = deepcopy(config)
    private = backup.private_configuration_snapshot(config)
    tools = yaml.safe_load(private["functions"])
    assert SECRET in json.dumps(tools)
    if kind in TEMPLATES:
        assert tools[0]["function"]["resource_template"] == TEMPLATES[kind]
    assert config == original


@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("exporter", ["setup", "legacy"])
def test_nonsecret_payloads_schemas_and_literal_templates_survive_complete_exports(
    persisted, exporter
):
    tool = _rest_tool()
    tool["function"].pop("headers")
    tool["function"].pop("resource")
    tool["function"]["resource_template"] = "https://weather.example.test/api?units=c"
    tool["function"]["params"] = {"units": "c", "limit": "20"}
    script = deepcopy(tool)
    script["spec"]["name"] = "script_payload"
    script["spec"]["parameters"] = {
        "type": "object",
        "properties": {
            key: {"type": "string", "default": "public-value"} for key in ALIASES
        },
    }
    script["function"] = {
        "type": "script",
        "sequence": [
            {
                "variables": {
                    "params": {key: "public-value" for key in ALIASES},
                    "example": {"type": "rest", "params": {"key": "public-value"}},
                }
            }
        ],
    }
    tools = [tool, script]
    config = {"functions": yaml.safe_dump(tools) if persisted else tools}
    original = deepcopy(config)
    _result, public = export(config, exporter)
    assert public["functions"] == tools
    assert config == original


@pytest.mark.parametrize(
    "key", ["key", "KEY", "sig", "Signature", "credential", "%6Bey"]
)
def test_query_strings_and_http_params_share_one_policy(key):
    literal = redact_secrets({"resource": f"https://example.test/?{key}={SECRET}"})
    params = redact_secrets(
        {"type": "rest", "resource": "https://example.test/", "params": {key: SECRET}}
    )
    assert SECRET not in json.dumps(literal)
    assert SECRET not in json.dumps(params)
    assert params["params"][key] == REDACTED_SECRET_SENTINEL


@pytest.mark.parametrize("kind", [[], {"invalid": "type"}])
def test_malformed_quarantined_types_do_not_crash_http_context_detection(kind):
    value = {
        "functions": [{"function": {"type": kind, "params": {"key": "public-value"}}}]
    }
    assert redact_secrets(value) == value


@pytest.mark.parametrize(
    "source",
    ["relative/api?key=" + SECRET, "  https://weather.example.test/api?key=" + SECRET],
)
def test_nonliteral_resource_templates_cannot_authorize_destination_recovery(source):
    value = {"resource_template": source}
    public = redact_secrets(value)
    assert public == {"resource_template": {"__extended_openai_redacted_url__": None}}
    restored, kept, missing = transfer._restore_section_secrets(public, value)
    assert restored == {} and missing and not kept


@pytest.mark.parametrize("userinfo", [False, True])
@pytest.mark.parametrize("persisted", [False, True])
def test_literal_resource_templates_keep_destination_bound_recovery(
    userinfo, persisted
):
    tool = _rest_tool()
    request = tool["function"]
    request.pop("resource")
    request["resource_template"] = (
        f"https://user:{SECRET}@weather.example.test/api?units=c"
        if userinfo
        else f"https://weather.example.test/api?api_key={SECRET}&units=c"
    )
    config = {"functions": yaml.safe_dump([tool]) if persisted else [tool]}
    public = redact_secrets(config)
    assert SECRET not in json.dumps(public)
    marker = public["functions"][0]["function"]["resource_template"]
    assert isinstance(marker["__extended_openai_redacted_url__"], str)
    restored, kept, missing = transfer._restore_section_secrets(public, config)
    assert kept and not missing
    assert (
        restored["functions"][0]["function"]["resource_template"]
        == request["resource_template"]
    )
    marker["__extended_openai_redacted_url__"] = marker[
        "__extended_openai_redacted_url__"
    ].replace("weather.example.test", "other.example.test")
    restored, kept, missing = transfer._restore_section_secrets(public, config)
    assert missing and not kept and SECRET not in json.dumps(restored)
