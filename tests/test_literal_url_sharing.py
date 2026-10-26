"""Literal credential URLs cannot acquire public status from their field name."""

from copy import deepcopy
import json
from urllib.parse import quote_from_bytes

from hypothesis import given, settings, strategies as st
import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    PRIVATE_TRANSFER_VERSION,
    decode_document,
    encode_document,
)
from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    export_rule_pack,
    validate_rule_pack,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    is_redacted_url,
    redact_http_url,
    redact_secrets,
)
from custom_components.extended_openai_conversation_responses.sharing import (
    project_configuration,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_request_rules import MemoryStore, RequestRules, local_rule
from tests.test_selective_restore_concurrency import RestoreHarness
from tests.test_sharing_schema_context import configuration_artifact
from tests.test_sharing_schema_references import tool_with_schema
from tests.test_transfer import _portable_document

SECRET = "FABRICATED-literal-url-credential-42"
SPELLINGS = ["{}", " {}", "\t{}", "\r\n{}", "\x00 {}", "ht\ttps:\r\n//{}", "HTTPS://{}"]


def credential_uri(kind, spelling):
    uri = (
        "https://user:" + SECRET + "@example.invalid/snapshot?units=c"
        if kind == "userinfo"
        else "https://example.invalid/snapshot?units=c&sig=" + SECRET
    )
    return spelling.format(uri.removeprefix("https://") if "//{}" in spelling else uri)


def script_config(uri, *, nested=False, persisted=False):
    tool = tool_with_schema({"type": "object", "properties": {}})
    tool["function"] = {
        "type": "script",
        "variables": {"media": [{"snapshot_endpoint": uri}]}
        if nested
        else {"snapshot_endpoint": uri},
        "sequence": [{"variables": {"_function_result": "Success"}}],
    }
    return {"functions": yaml.safe_dump([tool]) if persisted else [tool]}


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("kind", ["query", "userinfo"])
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_script_url_credentials_are_excluded_from_final_artifacts(
    hass, format, persisted, nested, kind, spelling
):
    uri = credential_uri(kind, spelling)
    config = script_config(uri, nested=nested, persisted=persisted)
    before = deepcopy(config)
    agent_config_snapshot(config)
    assert redact_http_url(uri) is not None

    artifact, public, serialized = configuration_artifact(config, format)

    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]
    assert config == before


@pytest.mark.parametrize("selection", ["all", "selected", "group"])
@pytest.mark.parametrize("kind", ["query", "userinfo"])
@pytest.mark.parametrize("spelling", SPELLINGS)
async def test_rule_url_credentials_and_signatures_do_not_leave(
    hass, selection, kind, spelling
):
    uri = credential_uri(kind, spelling)
    rule = local_rule()
    rule["action"]["actions"] = [
        {"action": "rest_command.report", "data": {"media": [{"image": uri}]}}
    ]
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    before = manager.snapshot()
    assert SECRET in before["rules"][0]["action"]["canonical_signature"]

    pack = export_rule_pack(manager, selection, rule_ids=[rule["id"]], reviewed=True)

    assert SECRET not in json.dumps(pack)
    assert pack["rules"] == []
    assert pack["security"]["omitted"]
    assert validate_rule_pack(pack)["rules"] == []
    assert manager.snapshot() == before


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize(
    "value",
    [
        "https://example.invalid/public?units=c",
        " \thttps://example.invalid/public?units=c",
        "ordinary: text",
        "// This is [an ordinary comment",
        "{{ states('sensor.temperature') }}",
        "https is a word, not a URL: {{ value }}",
        "custom://example.invalid/public",
    ],
)
def test_public_urls_and_arbitrary_templates_stay_portable(hass, format, value):
    config = script_config(value)
    agent_config_snapshot(config)
    _artifact, public, _serialized = configuration_artifact(config, format)
    assert public["functions"] == config["functions"]


@pytest.mark.parametrize("selection", ["all", "selected", "group"])
async def test_public_rule_urls_stay_portable(hass, selection):
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": "rest_command.report",
            "data": {"image": " \thttps://example.invalid/public?units=c"},
        }
    ]
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    before = manager.snapshot()
    pack = export_rule_pack(manager, selection, rule_ids=[rule["id"]], reviewed=True)
    assert pack["rules"][0]["action"]["actions"] == rule["action"]["actions"]
    assert "canonical_signature" not in pack["rules"][0]["action"]
    assert validate_rule_pack(pack)["rules"]
    assert manager.snapshot() == before


@pytest.mark.parametrize(
    "annotation", ["default", "example", "examples", "const", "enum"]
)
def test_schema_instance_urls_receive_the_same_parser_normalization(hass, annotation):
    uri = credential_uri("query", "\t{}")
    tool = tool_with_schema(
        {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    annotation: [uri] if annotation in {"examples", "enum"} else uri,
                }
            },
        }
    )
    agent_config_snapshot({"functions": [tool]})
    artifact, public, serialized = configuration_artifact(
        {"functions": [tool]}, "setup"
    )
    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]


def test_literal_http_templates_are_omitted_without_rendering(hass):
    config = script_config("https://{{ private_host }}/snapshot?sig={{ private_key }}")
    agent_config_snapshot(config)
    artifact, public, _serialized = configuration_artifact(config, "setup")
    assert public["functions"] == []
    assert artifact["security"]["omitted"]


@pytest.mark.parametrize(
    "value", ["https://user:private@[", " \thttps://user:private@["]
)
def test_malformed_http_authority_cannot_hide_from_literal_classification(hass, value):
    config = script_config(value)
    _artifact, public, _serialized = configuration_artifact(config, "setup")
    assert public["functions"] == []


@settings(max_examples=75, deadline=None)
@given(
    st.binary(min_size=1, max_size=40),
    st.text(alphabet="".join(chr(code) for code in range(33)), max_size=4),
)
def test_field_renaming_does_not_make_a_signed_url_public(generated, prefix):
    # The fabricated credential alphabet is irrelevant: sig supplies the meaning.
    uri = (
        prefix
        + "https://example.invalid/snapshot?sig="
        + quote_from_bytes(generated, safe="")
    )
    for name in ("url", "snapshot_endpoint", "image"):
        source = {name: uri}
        for schema_roots in (False, True):
            redacted = redact_secrets(source, schema_roots=schema_roots)
            assert is_redacted_url(redacted[name])
        assert source[name] == uri


@pytest.mark.parametrize("change", [None, "host", "path", "query"])
def test_legacy_recovery_of_newly_classified_urls_stays_destination_bound(change):
    config = script_config(credential_uri("query", "\t{}"))
    public = redact_secrets(config)
    destination = deepcopy(config)
    variables = destination["functions"][0]["function"]["variables"]
    if change == "host":
        variables["snapshot_endpoint"] = variables["snapshot_endpoint"].replace(
            "example.invalid", "other.invalid"
        )
    elif change == "path":
        variables["snapshot_endpoint"] = variables["snapshot_endpoint"].replace(
            "/snapshot", "/changed"
        )
    elif change == "query":
        variables["snapshot_endpoint"] += "&extra=1"
    restored, kept, missing = transfer._restore_section_secrets(public, destination)
    if change is None:
        assert restored == config
        assert kept and not missing
    else:
        assert missing and not kept
        assert SECRET not in json.dumps(restored)


@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("kind", ["query", "userinfo"])
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_private_backup_keeps_literal_uri_bytes(hass, persisted, kind, spelling):
    document = private_document()
    uri = credential_uri(kind, spelling)
    document["agent"]["config"].update(script_config(uri, persisted=persisted))
    before = deepcopy(document)

    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])

    assert decode_document(artifact) == before
    assert (
        transfer.inspect_transfer(artifact, "new-agent").config
        == before["agent"]["config"]
    )
    assert document == before


@pytest.mark.parametrize("persisted", [False, True])
def test_custom_private_transfer_keeps_original_uri_bytes(hass, persisted):
    document = _portable_document(mode="custom")
    document["version"] = PRIVATE_TRANSFER_VERSION
    config = script_config(credential_uri("query", "\x00 {}"), persisted=persisted)
    document["sections"]["configuration"] = config
    artifact = json.loads(json.dumps(encode_document(document)))
    assert decode_document(artifact) == document
    assert transfer.inspect_transfer(artifact, "new-agent").config == config


@pytest.mark.parametrize("persisted", [False, True])
async def test_private_restore_applies_exact_literal_credentials(
    hass, monkeypatch, persisted
):
    tx = RestoreHarness(monkeypatch)
    document = private_document()
    document["agent"]["config"].update(
        script_config(credential_uri("userinfo", "\x00 {}"), persisted=persisted)
    )
    expected = deepcopy(document["agent"]["config"])
    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])

    await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        artifact,
        sections=[transfer.SECTION_CONFIGURATION],
    )

    assert tx.subentry.data == expected


def test_restricted_projection_still_excludes_literal_url_tools():
    config = script_config(credential_uri("query", "{}"))
    public, summary = project_configuration(config)
    assert public["functions"] == []
    assert summary["omitted"]
    assert SECRET not in json.dumps([public, summary])
