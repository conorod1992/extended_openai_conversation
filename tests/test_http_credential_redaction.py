"""HTTP credentials stay private while portable recovery binds their destination."""

from copy import deepcopy
import json

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    redact_secrets,
)
from tests.test_backup import _document
from tests.test_secret_restore_context import _rest_tool
from tests.test_transfer import _portable_document

SECRET = "EOAI_AUDIT_HTTP_SECRET"


def configuration(kind, persisted=False):
    tool = _rest_tool()
    request = tool["function"]
    if kind == "cookie":
        request["headers"]["cOoKiE"] = f"sessionid={SECRET}"
    elif kind == "userinfo":
        request["resource"] = (
            f"https://apiuser:{SECRET}@weather.example.test/api?units=c"
        )
    else:
        request["resource"] = (
            f"https://weather.example.test/api?units=c&api%5Fkey={SECRET}&units=f"
        )
    tools = [tool]
    return {
        "chat_model": "gpt-4o",
        "functions": yaml.safe_dump(tools) if persisted else tools,
    }


@pytest.mark.parametrize("kind", ["cookie", "userinfo", "query"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("exporter", ["setup", "legacy"])
def test_http_credentials_absent_from_complete_export(kind, persisted, exporter):
    config = configuration(kind, persisted)
    original = deepcopy(config)
    if exporter == "setup":
        doc = _portable_document(mode="setup")
        doc["sections"]["configuration"] = config
        result = transfer.finalize_setup_export(doc)
        public = result["document"]["sections"]["configuration"]
    else:
        doc = _document()
        doc["agent"]["config"] = config
        result = backup.finalize_backup_snapshot(doc)
        public = result["document"]["agent"]["config"]
    assert SECRET not in result["json"]
    assert SECRET not in json.dumps(public)
    assert config == original
    assert redact_secrets(public) == public


@pytest.mark.parametrize("kind", ["cookie", "userinfo", "query"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("change", ["none", "host", "path", "method", "query"])
def test_http_credential_recovery_requires_public_destination(kind, persisted, change):
    config = configuration(kind, persisted)
    public = redact_secrets(config)
    decoded = deepcopy(public)
    tool = decoded["functions"][0]["function"]
    if change == "method":
        tool["method"] = "POST"
    elif change != "none":
        resource = tool["resource"]
        # URL redaction retains the public destination in its marker.
        key = next(iter(resource)) if isinstance(resource, dict) else None
        text = resource[key] if key else resource
        if change == "host":
            text = text.replace("weather.example.test", "other.example.test")
        elif change == "path":
            text = text.replace("/api", "/changed")
        else:
            text += "&added=1" if "?" in text else "?added=1"
        if key:
            resource[key] = text
        else:
            tool["resource"] = text
    restored, kept, missing = transfer._restore_section_secrets(decoded, config)
    if change == "none":
        assert not missing and kept
        assert (
            restored["functions"][0]["function"]
            == configuration(kind)["functions"][0]["function"]
        )
    else:
        assert missing and not kept
        assert SECRET not in json.dumps(restored)


def test_cookie_words_and_public_urls_are_preserved():
    value = {
        "Cookie": "ordinary preference",
        "notes": f"Cookie: {SECRET}",
        "resource": "https://example.test/api?q=carpet&limit=20",
        "headers": {"Accept": "application/json"},
        "spec": {"parameters": {"properties": {"Cookie": {"type": "string"}}}},
    }
    assert redact_secrets(value) == value


@pytest.mark.parametrize(
    "url",
    [
        "https://user:EOAI_AUDIT_HTTP_SECRET@example.test:8443/a?x=1#part",
        "https://example.test/a?access_token=EOAI_AUDIT_HTTP_SECRET&x=1",
        "https://example.test/a?X-API-Key=EOAI_AUDIT_HTTP_SECRET&X-API-Key=second-secret",
        "https://example.test/a?sig=EOAI_AUDIT_HTTP_SECRET&expires=tomorrow",
    ],
)
def test_url_marker_is_recoverable_only_for_same_public_url(url):
    from custom_components.extended_openai_conversation_responses.secret_redaction import (
        restore_redacted_secrets,
    )

    raw = {"url": url}
    public = redact_secrets(raw)
    assert SECRET not in json.dumps(public)
    assert restore_redacted_secrets(public) == {}
    restored, kept, missing = transfer._restore_section_secrets(public, raw)
    assert restored == raw and kept and not missing
    for changed in (
        url.replace("https:", "http:"),
        url.replace("/a", "/b"),
        url.replace("example.test", "other.test"),
    ):
        restored, kept, missing = transfer._restore_section_secrets(
            public, {"url": changed}
        )
        assert missing and not kept and restored == {}


def test_http_schema_defaults_and_private_snapshot_remain_unchanged():
    config = configuration("query", True)
    private = backup.private_configuration_snapshot(config)
    assert SECRET in private["functions"]
    schema = {
        "functions": [
            {
                "spec": {
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "resource": {
                                "type": "string",
                                "default": f"https://example.test/?api_key={SECRET}",
                            },
                            "headers": {
                                "type": "object",
                                "properties": {
                                    "Cookie": {
                                        "type": "string",
                                        "default": "ordinary cookie",
                                    }
                                },
                            },
                        },
                    }
                }
            }
        ]
    }
    assert redact_secrets(schema) == schema


@pytest.mark.parametrize(
    "url",
    [
        "https://[invalid/?api_key=EOAI_AUDIT_HTTP_SECRET",
        "https://example.test/sk-1234567890123456?api_key=EOAI_AUDIT_HTTP_SECRET",
    ],
)
def test_unsupported_url_markers_cannot_authorize_hidden_destinations(url):
    public = redact_secrets({"resource": url})
    assert SECRET not in json.dumps(public)
    assert redact_secrets(public) == public
    restored, kept, missing = transfer._restore_section_secrets(
        public, {"resource": url}
    )
    assert missing and not kept and restored == {}


@pytest.mark.parametrize(
    "url",
    [
        "https://user:EOAI_AUDIT_HTTP_SECRET@example.test/api",
        "https://example.test/api?api_key=EOAI_AUDIT_HTTP_SECRET",
        "EOAI_AUDIT_HTTP_SECRET",
    ],
)
def test_marker_shaped_stored_values_are_not_trusted_on_export(url):
    # Use the wire key so this assertion also runs on the original revision.
    stored = {"resource": {"__extended_openai_redacted_url__": url}}
    original = deepcopy(stored)
    public = redact_secrets(stored)
    assert SECRET not in json.dumps(public)
    assert redact_secrets(public) == public
    assert stored == original
