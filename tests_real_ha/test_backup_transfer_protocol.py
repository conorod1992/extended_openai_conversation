"""Real Home Assistant acceptance tests for backup/export/import transport."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import (
    CLIENT_ID,
    MockConfigEntry,
    MockUser,
    async_test_home_assistant,
)

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.backup_transfer import (
    WS_BACKUP_TRANSFER,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    async_get_request_rules,
)
from custom_components.extended_openai_conversation_responses.transfer import (
    SECTION_CONFIGURATION,
    SECTION_PERSISTENT_MEMORY,
    SECTION_REQUEST_RULES,
)
from homeassistant import loader
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import recorder as recorder_helper
from homeassistant.setup import async_setup_component


def _entry() -> MockConfigEntry:
    """Build one local-only entry with durable Memory enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Backup Transfer Acceptance",
        data={
            CONF_API_KEY: "sk-backup-transfer-acceptance",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {CONF_MEMORY_MODE: MEMORY_MODE_MANUAL},
                "subentry_type": "conversation",
                "title": "Backup Transfer Conversation",
                "unique_id": None,
            }
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Load the integration through Home Assistant's real config-entry manager."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _conversation_subentry(entry: MockConfigEntry):
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


async def _user_token(hass: HomeAssistant, user: MockUser) -> str:
    """Create a real Home Assistant access token for one test user."""
    user.add_to_hass(hass)
    refresh_token = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    return hass.auth.async_create_access_token(refresh_token)


async def _transfer_call(
    client: Any,
    *,
    entry: MockConfigEntry,
    action: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the registered backup-transfer WebSocket command."""
    subentry = _conversation_subentry(entry)
    await client.send_json_auto_id(
        {
            "type": WS_BACKUP_TRANSFER,
            "action": action,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "data": data or {},
        }
    )
    return await client.receive_json()


async def _download_archive(
    client: Any,
    *,
    entry: MockConfigEntry,
    mode: str,
    sections: list[str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Drive export_start/export_chunk and return the reconstructed archive."""
    start_data: dict[str, Any] = {"mode": mode}
    if sections is not None:
        start_data["sections"] = sections
    started = await _transfer_call(
        client, entry=entry, action="export_start", data=start_data
    )
    assert started["success"], started
    metadata = started["result"]
    session_id = metadata["session_id"]

    chunks: list[bytes] = []
    try:
        for index in range(metadata["chunk_count"]):
            response = await _transfer_call(
                client,
                entry=entry,
                action="export_chunk",
                data={"session_id": session_id, "index": index},
            )
            assert response["success"], response
            result = response["result"]
            decoded = base64.b64decode(result["data"], validate=True)
            assert len(decoded) == result["bytes"]
            assert result["index"] == index
            assert result["final"] is (index == metadata["chunk_count"] - 1)
            chunks.append(decoded)
    finally:
        cancelled = await _transfer_call(
            client,
            entry=entry,
            action="export_cancel",
            data={"session_id": session_id},
        )
        assert cancelled["success"], cancelled
        assert cancelled["result"] == {"cancelled": True}

    archive = b"".join(chunks)
    assert len(archive) == metadata["size"]
    assert hashlib.sha256(archive).hexdigest() == metadata["sha256"]
    return archive, metadata


async def _upload_archive(
    client: Any,
    *,
    entry: MockConfigEntry,
    archive: bytes,
    filename: str,
) -> str:
    """Drive import_start/import_chunk and return the completed upload session."""
    started = await _transfer_call(
        client,
        entry=entry,
        action="import_start",
        data={"filename": filename, "size": len(archive)},
    )
    assert started["success"], started
    result = started["result"]
    session_id = result["session_id"]
    chunk_size = result["chunk_size"]

    for index, offset in enumerate(range(0, len(archive), chunk_size)):
        chunk = archive[offset : offset + chunk_size]
        response = await _transfer_call(
            client,
            entry=entry,
            action="import_chunk",
            data={
                "session_id": session_id,
                "index": index,
                "data": base64.b64encode(chunk).decode("ascii"),
            },
        )
        assert response["success"], response
        uploaded = response["result"]
        assert uploaded["received"] == min(offset + chunk_size, len(archive))
        assert uploaded["next_index"] == index + 1
        assert uploaded["complete"] is (offset + chunk_size >= len(archive))

    return session_id


async def _inspect_json_upload(client, entry, document, *, inspect_only):
    session_id = await _upload_archive(
        client,
        entry=entry,
        archive=json.dumps(document).encode(),
        filename="backup.json",
    )
    try:
        return await _transfer_call(
            client,
            entry=entry,
            action="import_inspect",
            data={"session_id": session_id, "inspect_only": inspect_only},
        )
    finally:
        cancelled = await _transfer_call(
            client, entry=entry, action="import_cancel", data={"session_id": session_id}
        )
        assert cancelled["success"], cancelled


@pytest.mark.parametrize("kind", ["full", "custom", "setup", "legacy"])
async def test_uploaded_backup_reports_malformed_version_and_mode(
    hass, hass_ws_client, kind
):
    from custom_components.extended_openai_conversation_responses import (
        backup,
        transfer,
    )
    from custom_components.extended_openai_conversation_responses.private_backup import (
        encode_document,
    )
    from tests.helpers import historical_shareable_export, legacy_agent_export

    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    client = await hass_ws_client(
        hass, await _user_token(hass, MockUser(is_owner=True))
    )
    if kind == "full":
        document = encode_document(
            await backup.async_collect_backup_snapshot(hass, entry, subentry)
        )
    elif kind == "custom":
        document = encode_document(
            await transfer.async_collect_transfer_snapshot(
                hass, entry, subentry, sections=[SECTION_CONFIGURATION]
            )
        )
    elif kind == "setup":
        document = historical_shareable_export("setup")
    else:
        document = legacy_agent_export(subentry)

    valid = await _inspect_json_upload(client, entry, document, inspect_only=True)
    assert valid["success"] and valid["result"]["valid"], valid
    cases = [
        ("version", value, "unsupported")
        for value in ([], {}, True, float(document["version"]))
    ]
    if kind in {"custom", "setup"}:
        cases.extend(
            ("mode", value, "transfer mode is invalid") for value in ([], {}, True)
        )
    for field, value, expected in cases:
        rejected = await _inspect_json_upload(
            client, entry, {**document, field: value}, inspect_only=True
        )
        assert not rejected["success"], (field, value, rejected)
        assert expected in rejected["error"]["message"], (field, value, rejected)


async def test_uploaded_backup_rejects_oversized_daily_usage(hass, hass_ws_client):
    from custom_components.extended_openai_conversation_responses import backup, usage
    from custom_components.extended_openai_conversation_responses.private_backup import (
        encode_document,
    )

    entry = _entry()
    await _setup_entry(hass, entry)
    client = await hass_ws_client(
        hass, await _user_token(hass, MockUser(is_owner=True))
    )
    snapshot = await backup.async_collect_backup_snapshot(
        hass, entry, _conversation_subentry(entry)
    )
    date = "2026-11-01"
    snapshot["usage"]["daily"][date] = usage._empty_day(date) | {
        "run_count": 1,
        "run_total_tokens": 10**309,
    }
    response = await _inspect_json_upload(
        client, entry, encode_document(snapshot), inspect_only=False
    )
    assert not response["success"], response
    assert "usage counter run_total_tokens is invalid" in response["error"]["message"]


@pytest.mark.parametrize(
    ("kind", "source_kind"),
    [("setup", "portable_transfer"), ("configuration", "legacy_setup")],
)
async def test_retired_public_files_restore_through_registered_websocket(
    hass, hass_ws_client, kind, source_kind,
):
    from tests.helpers import historical_shareable_export

    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    user = MockUser(id="legacy-import-admin", name="Legacy import admin", is_owner=True)
    client = await hass_ws_client(hass, await _user_token(hass, user))

    retired = await _transfer_call(client, entry=entry, action="setup_export")
    assert not retired["success"]
    assert "Unsupported export/import transfer action" in retired["error"]["message"]

    source = historical_shareable_export(kind)
    session_id = await _upload_archive(
        client,
        entry=entry,
        archive=json.dumps(source).encode(),
        filename=f"shareable-{kind}.json",
    )
    selected = [SECTION_CONFIGURATION]
    inspected = await _transfer_call(
        client, entry=entry, action="import_inspect",
        data={"session_id": session_id, "sections": selected},
    )
    assert inspected["success"], inspected
    result = inspected["result"]
    assert result["valid"]
    assert result["source_kind"] == source_kind
    assert result["summary"]["security"]["mode"] == "restricted"
    assert result["preview"]["selected_sections"] == selected
    assert result["preview_token"]

    restored = await _transfer_call(
        client, entry=entry, action="import_restore",
        data={
            "session_id": session_id, "sections": selected,
            "preview_token": result["preview_token"],
        },
    )
    assert restored["success"], restored
    assert restored["result"]["status"] == "restored"
    current = hass.config_entries.async_get_entry(entry.entry_id)
    assert current.subentries[subentry.subentry_id].data["max_tokens"] == 550


async def test_private_backup_moves_request_rules_between_installations(
    hass: HomeAssistant,
    hass_ws_client: Any,
    real_store_io,
    tmp_path: Path,
) -> None:
    """A rules-only ZIP crosses independent HA instances and persists on the target."""
    source = _entry()
    await _setup_entry(hass, source)
    source_agent = _conversation_subentry(source)
    source_rules = await async_get_request_rules(
        hass, source.entry_id, source_agent.subentry_id
    )
    first = {
        "id": "source-first",
        "name": "Source first",
        "enabled": True,
        "phrases": ["answer {question}"],
        "match_type": "sentence_pattern",
        "action_type": "model_routing",
        "action": {
            "model": "gpt-5-mini",
            "scope": "request",
            "continue_to_ai": True,
        },
        "ai_input_mode": "capture",
        "ai_input_capture": "question",
        "group_id": "source-group",
        "order": 0,
    }
    second = {
        **first,
        "id": "source-second",
        "name": "Source second",
        "enabled": False,
        "phrases": ["summarize {question}"],
        "order": 1,
    }
    await source_rules.async_replace_backup(
        {
            "rules": [first, second],
            "groups": [{"id": "source-group", "name": "Source group"}],
            "defaults": {**DEFAULT_MATCHING, "word_forms": False},
            "wording_groups": [{"canonical": "answer", "alternatives": ["respond"]}],
        }
    )
    expected = await source_rules.async_backup_data()
    source_admin = MockUser(id="source-admin", name="Source admin", is_owner=True)
    source_client = await hass_ws_client(hass, await _user_token(hass, source_admin))

    archive, metadata = await _download_archive(
        source_client, entry=source, mode="custom", sections=[SECTION_REQUEST_RULES]
    )
    await source_client.close()
    destination_dir = tmp_path / "separate-installation"
    destination_dir.mkdir()
    async with async_test_home_assistant(
        config_dir=str(destination_dir)
    ) as target_hass:
        client = None
        try:
            target_hass.data[loader.DATA_CUSTOM_COMPONENTS] = dict(
                hass.data[loader.DATA_CUSTOM_COMPONENTS]
            )
            assert await async_setup_component(target_hass, "homeassistant", {})
            recorder_helper.async_initialize_recorder(target_hass)
            target = _entry()
            await _setup_entry(target_hass, target)
            target_agent = _conversation_subentry(target)
            target_rules = await async_get_request_rules(
                target_hass, target.entry_id, target_agent.subentry_id
            )
            await target_rules.async_create(
                {**first, "name": "Destination only", "group_id": None}
            )
            admin = MockUser(id="target-admin", name="Target admin", is_owner=True)
            client = await hass_ws_client(
                target_hass, await _user_token(target_hass, admin)
            )
            session = await _upload_archive(
                client, entry=target, archive=archive, filename=metadata["filename"]
            )
            inspected = await _transfer_call(
                client,
                entry=target,
                action="import_inspect",
                data={"session_id": session},
            )
            assert inspected["success"], inspected
            preview = inspected["result"]
            assert preview["available_sections"] == [SECTION_REQUEST_RULES]
            assert preview["preview"]["selected_sections"] == [SECTION_REQUEST_RULES]
            restored = await _transfer_call(
                client,
                entry=target,
                action="import_restore",
                data={"session_id": session, "preview_token": preview["preview_token"]},
            )
            assert restored["success"], restored
            assert restored["result"]["status"] == "restored"
            assert await target_rules.async_backup_data() == expected
            assert await target_hass.config_entries.async_reload(target.entry_id)
            reloaded = await async_get_request_rules(
                target_hass, target.entry_id, target_agent.subentry_id
            )
            assert reloaded is not target_rules
            assert await reloaded.async_backup_data() == expected
        finally:
            if client is not None:
                await client.close()
            await target_hass.async_stop()
    assert await source_rules.async_backup_data() == expected


@pytest.mark.asyncio
async def test_full_backup_round_trip_through_registered_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A real admin client can export, inspect, and atomically restore a full backup."""
    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    registered = conversation.async_get_agent(hass, entry.entry_id)
    admin = MockUser(id="backup-admin", name="Backup Admin", is_owner=True)
    admin_client = await hass_ws_client(hass, await _user_token(hass, admin))

    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    owner = admin.id
    created = await memory.async_add(
        owner,
        "Original full-backup acceptance marker.",
        "acceptance",
        "explicit",
    )
    original_id = created["memory"]["memory_id"]

    archive, metadata = await _download_archive(admin_client, entry=entry, mode="full")
    assert metadata["mode"] == "full"
    assert metadata["content_type"] == "application/zip"
    assert metadata["filename"].endswith(".zip")

    assert await memory.async_delete(owner, [original_id]) == 1
    replacement = await memory.async_add(
        owner,
        "Post-export state that must be replaced by restore.",
        "acceptance",
        "explicit",
    )
    replacement_id = replacement["memory"]["memory_id"]

    import_session = await _upload_archive(
        admin_client,
        entry=entry,
        archive=archive,
        filename=metadata["filename"],
    )

    inspected = await _transfer_call(
        admin_client,
        entry=entry,
        action="import_inspect",
        data={"session_id": import_session},
    )
    assert inspected["success"], inspected
    inspection = inspected["result"]
    assert inspection["valid"] is True
    assert inspection["source_kind"] == "full_backup"
    assert inspection["summary"]["persistent_memories"] == 1
    assert SECTION_PERSISTENT_MEMORY in inspection["available_sections"]
    assert SECTION_PERSISTENT_MEMORY in inspection["preview"]["selected_sections"]

    restored = await _transfer_call(
        admin_client,
        entry=entry,
        action="import_restore",
        data={
            "session_id": import_session,
            "preview_token": inspection["preview_token"],
        },
    )
    assert restored["success"], restored
    assert restored["result"]["status"] == "restored"
    assert (
        SECTION_PERSISTENT_MEMORY in restored["result"]["transfer"]["selected_sections"]
    )

    await hass.async_block_till_done()
    assert conversation.async_get_agent(hass, entry.entry_id) is registered
    memories = await memory.async_list(owner)
    assert [(item.memory_id, item.content) for item in memories] == [
        (original_id, "Original full-backup acceptance marker.")
    ]
    assert all(item.memory_id != replacement_id for item in memories)

    replay = await _transfer_call(
        admin_client,
        entry=entry,
        action="import_restore",
        data={"session_id": import_session},
    )
    assert not replay["success"]
    assert replay["error"]["code"] == "invalid_request"
    assert "expired or does not exist" in replay["error"]["message"]


@pytest.mark.asyncio
async def test_backup_transfer_rejects_non_admin_through_registered_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A normal authenticated user is rejected by HA's real admin boundary."""
    entry = _entry()
    await _setup_entry(hass, entry)

    normal_user = MockUser(id="backup-normal", name="Backup Normal User")
    normal_client = await hass_ws_client(hass, await _user_token(hass, normal_user))
    denied = await _transfer_call(
        normal_client,
        entry=entry,
        action="export_start",
        data={"mode": "custom", "sections": [SECTION_PERSISTENT_MEMORY]},
    )
    assert not denied["success"], denied
    assert denied["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_fails_before", ["preview", "restore"])
async def test_custom_backup_selection_round_trip_through_registered_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    storage_fails_before: str,
) -> None:
    """An unavailable unselected store does not prevent a Memory restore."""
    from custom_components.extended_openai_conversation_responses import (
        knowledge,
        request_rules,
    )

    unavailable_knowledge = AsyncMock(side_effect=OSError("Knowledge storage failed"))
    unavailable_rules = AsyncMock(side_effect=OSError("Request Rules storage failed"))

    def fail_unselected_storage() -> None:
        monkeypatch.setattr(knowledge, "async_get_knowledge", unavailable_knowledge)
        monkeypatch.setattr(request_rules, "async_get_request_rules", unavailable_rules)

    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    registered = conversation.async_get_agent(hass, entry.entry_id)
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    original_config = agent_config_snapshot(subentry.data)
    original_raw_config = dict(subentry.data)
    admin = MockUser(
        id="custom-backup-admin", name="Custom Backup Admin", is_owner=True
    )
    owner = admin.id
    original = await memory.async_add(
        owner,
        "Custom backup acceptance marker.",
        "acceptance",
        "explicit",
    )
    original_id = original["memory"]["memory_id"]

    admin_client = await hass_ws_client(hass, await _user_token(hass, admin))
    archive, metadata = await _download_archive(
        admin_client,
        entry=entry,
        mode="custom",
        sections=[SECTION_PERSISTENT_MEMORY],
    )
    assert metadata["mode"] == "custom"

    assert await memory.async_delete(owner, [original_id]) == 1
    await memory.async_add(
        owner,
        "Custom backup replacement marker.",
        "acceptance",
        "explicit",
    )

    import_session = await _upload_archive(
        admin_client,
        entry=entry,
        archive=archive,
        filename=metadata["filename"],
    )
    try:
        if storage_fails_before == "preview":
            fail_unselected_storage()
        inspected = await _transfer_call(
            admin_client,
            entry=entry,
            action="import_inspect",
            data={"session_id": import_session},
        )
        assert inspected["success"], inspected
        inspection = inspected["result"]
        assert inspection["source_kind"] == "custom_backup"
        assert inspection["available_sections"] == [SECTION_PERSISTENT_MEMORY]
        assert inspection["preview"]["selected_sections"] == [SECTION_PERSISTENT_MEMORY]
        assert inspection["can_create_new_agent"] is False

        if storage_fails_before == "restore":
            fail_unselected_storage()
        restored = await _transfer_call(
            admin_client,
            entry=entry,
            action="import_restore",
            data={
                "session_id": import_session,
                "preview_token": inspection["preview_token"],
            },
        )
        assert restored["success"], restored
        assert restored["result"]["transfer"]["selected_sections"] == [
            SECTION_PERSISTENT_MEMORY
        ]
        import_session = ""
    finally:
        if import_session:
            cancelled = await _transfer_call(
                admin_client,
                entry=entry,
                action="import_cancel",
                data={"session_id": import_session},
            )
            assert cancelled["success"], cancelled

    await hass.async_block_till_done()
    assert conversation.async_get_agent(hass, entry.entry_id) is registered
    memories = await memory.async_list(owner)
    assert [(item.memory_id, item.content) for item in memories] == [
        (original_id, "Custom backup acceptance marker.")
    ]

    assert dict(subentry.data) == original_raw_config
    assert agent_config_snapshot(subentry.data) == original_config
    unavailable_knowledge.assert_not_awaited()
    unavailable_rules.assert_not_awaited()


@pytest.mark.parametrize("route_owner", ["target", "sibling"])
async def test_private_provider_restore_checks_preserved_routes_through_websocket(
    hass: HomeAssistant, hass_ws_client: Any, hass_storage, route_owner: str
) -> None:
    """An enabled Azure route must stop a switch to a compatible OpenAI endpoint."""
    from custom_components.extended_openai_conversation_responses.request_rules import (
        async_get_request_rules,
    )
    from tests_real_ha.test_acceptance_lifecycle import _make_entry, _subentry

    source = _make_entry(
        "Source",
        include_ai_task=False,
        base_url="http://local-provider.example/v1",
        data={"api_key": "source-key", "organization": None},
    )
    await _setup_entry(hass, source)
    target = MockConfigEntry(
        domain=DOMAIN,
        title="Destination",
        data={
            "api_key": "destination-key",
            "api_provider": "azure",
            "base_url": "https://destination.openai.azure.com",
            "api_version": "2025-04-01-preview",
            "skip_authentication": True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            _subentry(
                "conversation", "Selected assistant", {"chat_model": "gpt-4.1-mini"}
            ),
            _subentry(
                "conversation", "Sibling assistant", {"chat_model": "gpt-4.1-mini"}
            ),
            _subentry(
                "ai_task_data", "Sibling AI Task", {"chat_model": "gpt-4.1-mini"}
            ),
        ],
    )
    await _setup_entry(hass, target)
    target_agent = _conversation_subentry(target)
    route_agent = (
        target_agent
        if route_owner == "target"
        else next(
            child
            for child in target.subentries.values()
            if child.title == "Sibling assistant"
        )
    )
    rules = await async_get_request_rules(
        hass, target.entry_id, route_agent.subentry_id
    )
    saved = await rules.async_create(
        {
            "name": "Azure deployment route",
            "phrases": ["use another deployment"],
            "match_type": "starts_with",
            "action_type": "model_routing",
            "action": {
                "model": "other-deployment",
                "azure_model": "gpt-4.1-mini",
                "scope": "conversation",
                "continue_to_ai": True,
            },
        }
    )
    admin = MockUser(
        id="provider-restore-admin", name="Provider Restore Admin", is_owner=True
    )
    client = await hass_ws_client(hass, await _user_token(hass, admin))
    archive, metadata = await _download_archive(client, entry=source, mode="full")

    async def upload():
        session_id = await _upload_archive(
            client, entry=target, archive=archive, filename=metadata["filename"]
        )
        return {
            "session_id": session_id,
            "sections": ["configuration"],
            "restore_provider_connection": True,
        }

    def state():
        return deepcopy(
            (
                dict(target.data),
                {
                    key: (child.title, dict(child.data))
                    for key, child in target.subentries.items()
                },
                {
                    key: value
                    for key, value in hass_storage.items()
                    if DOMAIN in key or key == "core.config_entries"
                },
                rules.snapshot(),
            )
        )

    data = await upload()
    before = state()
    rejected = await _transfer_call(
        client, entry=target, action="import_inspect", data=data
    )
    assert not rejected["success"]
    assert "Azure deployment route" in rejected["error"]["message"]
    if route_owner == "sibling":
        assert "Sibling assistant" in rejected["error"]["message"]
    await hass.async_block_till_done()
    assert state() == before

    await rules.async_update(saved["id"], {**saved, "enabled": False})
    preview = await _transfer_call(
        client, entry=target, action="import_inspect", data=data
    )
    assert preview["success"], preview
    if route_owner == "sibling":
        # A sibling's changes do not alter the selected assistant's preview revision.
        # The restore must revalidate before the first durable mutation.
        await rules.async_update(saved["id"], {**saved, "enabled": True})
        before = state()
        rejected = await _transfer_call(
            client,
            entry=target,
            action="import_restore",
            data={**data, "preview_token": preview["result"]["preview_token"]},
        )
        assert not rejected["success"]
        assert "Azure deployment route" in rejected["error"]["message"]
        await hass.async_block_till_done()
        assert state() == before
        await rules.async_update(saved["id"], {**saved, "enabled": False})
        data = await upload()
        preview = await _transfer_call(
            client, entry=target, action="import_inspect", data=data
        )
        assert preview["success"], preview

    restored = await _transfer_call(
        client,
        entry=target,
        action="import_restore",
        data={**data, "preview_token": preview["result"]["preview_token"]},
    )
    assert restored["success"], restored
    assert restored["result"]["provider_connection_restored"] is True
    await hass.async_block_till_done()
    assert target.state is ConfigEntryState.LOADED
    assert dict(target.data) == dict(source.data)
    preserved = await async_get_request_rules(
        hass, target.entry_id, route_agent.subentry_id
    )
    assert preserved.rule_snapshot(saved["id"])["enabled"] is False


@pytest.fixture
def real_store_io():
    """Select native Store files for the cold sibling validation test."""
    return True


async def test_cold_provider_validation_reads_legacy_sibling_rules_without_migrating(
    hass: HomeAssistant, real_store_io
) -> None:
    from pathlib import Path

    from custom_components.extended_openai_conversation_responses.request_rules import (
        STORAGE_KEY_PREFIX,
        RequestRuleStore,
        async_read_committed_request_rules,
    )

    entry_id, sibling_id = "cold-provider-entry", "cold-provider-sibling"
    legacy_store = RequestRuleStore(
        hass, 1, f"{STORAGE_KEY_PREFIX}.{entry_id}.{sibling_id}"
    )
    await legacy_store.async_save(
        {
            "rules": [
                {
                    "id": "legacy-route",
                    "name": "Legacy model route",
                    "phrases": ["switch deployment"],
                    "match_type": "starts_with",
                    "action_type": "model_routing",
                    "action": {"model": "gpt-4.1-mini", "scope": "conversation"},
                }
            ]
        }
    )
    path = Path(legacy_store.path)
    original = await hass.async_add_executor_job(path.read_bytes)
    saved = await async_read_committed_request_rules(hass, entry_id, sibling_id)
    assert saved[0]["id"] == "legacy-route"
    assert saved[0]["enabled"] is True
    assert await hass.async_add_executor_job(path.read_bytes) == original
