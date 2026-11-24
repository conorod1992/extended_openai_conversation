"""Literal credential URLs cannot acquire public status from their field name."""

from copy import deepcopy
import json
from urllib.parse import quote_from_bytes

from hypothesis import given, settings, strategies as st
import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.private_backup import (
    PRIVATE_TRANSFER_VERSION,
    decode_document,
    encode_document,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    is_redacted_url,
    redact_secrets,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_selective_restore_concurrency import RestoreHarness
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
