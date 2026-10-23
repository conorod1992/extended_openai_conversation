"""OAuth fragment credentials are excluded from sharing and preserved privately."""

from copy import deepcopy
import json

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    export_rule_pack,
    validate_rule_pack,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    is_redacted_secret,
    redact_http_url,
    redact_secrets,
)
from tests.test_literal_url_sharing import script_config
from tests.test_private_backup_fidelity import private_document
from tests.test_request_rules import MemoryStore, RequestRules, local_rule
from tests.test_sharing_schema_context import configuration_artifact

SECRET = "FABRICATED-standard-oauth-bearer-42"
NAMES = ["access_token", "id_token", "%61ccess_token"]


def fragment_url(name):
    return (
        "https://app.example.invalid/callback#"
        + name
        + "="
        + SECRET
        + "&token_type=Bearer&expires_in=3600"
    )


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("persisted", [False, True])
def test_oauth_fragment_credentials_are_omitted_from_config_artifacts(
    hass, format, name, persisted
):
    uri = fragment_url(name)
    config = script_config(uri, persisted=persisted)
    before = deepcopy(config)
    agent_config_snapshot(config)
    assert is_redacted_secret(redact_secrets({"access_token": SECRET})["access_token"])
    assert redact_http_url(uri) is not None
    artifact, public, serialized = configuration_artifact(config, format)
    assert SECRET not in serialized
    assert public["functions"] == []
    assert artifact["security"]["omitted"]
    assert config == before


@pytest.mark.parametrize("selection", ["all", "selected", "group"])
@pytest.mark.parametrize("name", NAMES)
async def test_oauth_fragment_credentials_are_omitted_from_rule_packs(
    hass, selection, name
):
    rule = local_rule()
    rule["action"]["actions"] = [
        {"action": "rest_command.report", "data": {"callback": fragment_url(name)}}
    ]
    manager = RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    before = deepcopy(manager.snapshot())
    pack = export_rule_pack(manager, selection, rule_ids=[rule["id"]], reviewed=True)
    assert SECRET not in json.dumps(pack)
    assert pack["rules"] == []
    assert pack["security"]["omitted"]

    assert validate_rule_pack(pack)["rules"] == []
    assert manager.snapshot() == before
    assert SECRET not in json.dumps(
        export_rule_pack(manager, selection, rule_ids=[rule["id"]])
    )


@pytest.mark.parametrize("name", NAMES)
def test_oauth_fragment_private_control(hass, name):
    document = private_document()
    document["agent"]["config"].update(
        script_config(fragment_url(name), persisted=True)
    )
    before = deepcopy(document)
    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])
    assert decode_document(artifact) == before
    assert (
        transfer.inspect_transfer(artifact, "fresh-agent").config
        == before["agent"]["config"]
    )


@pytest.mark.parametrize("change", [None, "host", "path", "query", "fragment"])
def test_fragment_recovery_requires_the_same_public_destination(change):
    uri = (
        "https://app.example.invalid/callback?units=c#access_token="
        + SECRET
        + "&expires_in=3600"
    )
    source = {"url": uri}
    public = redact_secrets(source)
    assert SECRET not in json.dumps(public)
    assert redact_secrets(public) == public
    destination = deepcopy(source)
    replacements = {
        "host": ("app.example.invalid", "other.invalid"),
        "path": ("/callback", "/changed"),
        "query": ("units=c", "units=f"),
        "fragment": ("expires_in=3600", "expires_in=7200"),
    }
    if change:
        destination["url"] = uri.replace(*replacements[change])
    restored, kept, missing = transfer._restore_section_secrets(public, destination)
    if change is None:
        assert restored == source and kept and not missing
    else:
        assert restored == {} and missing and not kept


def test_public_fragment_bytes_and_multiple_secret_locations():
    public = "https://app.example.invalid/path?q=public#chapter-2&units=c"
    assert redact_http_url(public) is None
    uri = (
        "https://app.example.invalid/path?api_key="
        + SECRET
        + "#access_token="
        + SECRET
        + "&units=c"
    )
    marker = redact_http_url(uri)
    assert SECRET not in json.dumps(marker)
    assert marker["__extended_openai_redacted_url__"].endswith(
        "#access_token=[redacted]&units=c"
    )
