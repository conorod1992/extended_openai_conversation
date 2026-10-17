"""Regression coverage for selective restore and legacy configuration semantics."""

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    backup,
    continuity,
    management_loading_performance,
    management_ui,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_conversation_archive import FakeArchiveStorage


@pytest.mark.parametrize(
    "mode",
    [
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        CONVERSATION_CONTINUITY_USER,
        CONVERSATION_CONTINUITY_DEVICE,
    ],
)
async def test_private_archive_binding_after_actual_restore_cleanup(hass, mode):
    manager = continuity.ConversationContinuity("agent")
    hass.data[continuity._MANAGERS] = {("entry", "agent"): manager}
    scope = user_scope("owner", source="authenticated_user", device_id="device")
    first = await manager.async_resolve(mode, scope, "device", "original-ha-id", 30)
    original_id = first.conversation_id
    key = f"continuity:{first.key}" if first.key else f"conversation:{original_id}"
    archive = ConversationArchive(FakeArchiveStorage(), "agent")
    await archive.async_initialize()
    session = await archive.async_begin_session(
        key,
        scope,
        original_id,
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    await archive.async_make_private(session.session_id)
    await manager.async_record_success(first.key, first.claim_token, [])
    await manager.async_release(first.key, first.claim_token)
    saved = await archive.async_recovery_data()
    sessions, turns = archive.validate_backup_data(
        {k: saved[k] for k in ("sessions", "turns")}, "agent", local_recovery=True
    )
    # The restore and rollback paths both apply this preserved Archive generation
    # and call the same production runtime-cleanup function below.
    await archive.async_replace_backup(sessions, turns, active=saved["active"])
    restore_recovery.reset_restored_runtime(
        hass, "entry", "agent", (None, None, None, archive, None, None, None)
    )
    second = await manager.async_resolve(mode, scope, "device", original_id, 30)
    next_key = (
        f"continuity:{second.key}"
        if second.key
        else f"conversation:{second.conversation_id}"
    )
    next_session = await archive.async_begin_session(
        next_key,
        scope,
        second.conversation_id,
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    await archive.async_record_turn(
        next_session.session_id,
        run_id="followup",
        user_text="PRIVATE AFTER RESTORE",
        assistant_text="Private reply",
        successful=True,
    )
    exported = await archive.async_backup_data()
    retained = [
        t for t in exported["turns"] if t["user_text"] == "PRIVATE AFTER RESTORE"
    ]
    assert not retained
    assert next_session.retention_state == "private"
    assert second.conversation_id == original_id
    assert second.history == []
    assert second.discard_history is (mode != CONVERSATION_CONTINUITY_HA_DEFAULT)
    await manager.async_release(second.key, second.claim_token)


@pytest.mark.parametrize("prewarmed", [False, True])
@pytest.mark.parametrize("validated", [False, True])
async def test_legacy_blank_runtime_and_management_agree(hass, prewarmed, validated):
    raw = {**agent_config.agent_config_defaults(), "functions": ""}
    runtime = agent_config.configured_function_tools_from_data(raw)
    assert {t["spec"]["name"] for t in runtime} == {
        "execute_services",
        "get_attributes",
        "load_skill",
        "bash",
    }
    display = (
        management_loading_performance._snapshot_normalized_configuration(
            {**raw, "functions": agent_config.parse_configured_function_tools(raw)},
            validated=validated,
        )
        if prewarmed
        else agent_config.agent_config_snapshot(raw)
    )
    assert len(display["functions"]) == 4
    assert agent_config.configured_function_tools_from_data(raw)


async def test_legacy_blank_actual_duplicate_and_transfer(
    management_agent, management_message, hass
):
    _entry, subentry = management_agent
    subentry.data = {**subentry.data, "functions": ""}
    assert len(agent_config.configured_function_tools_from_data(subentry.data)) == 4
    await management_ui.async_management_command(
        hass, "admin", True, management_message("configuration", "duplicate")
    )
    duplicated = hass.config_entries.async_add_subentry.call_args.args[1]
    assert duplicated.data["functions"] != "[]\n"
    assert len(agent_config.configured_function_tools_from_data(duplicated.data)) == 4
    document = management_ui._export_agent(subentry)
    imported = management_ui._parse_import_document(document, destination=subentry)
    assert imported["config"]["functions"] != "[]\n"
    assert (
        len(agent_config.configured_function_tools_from_data(imported["config"])) == 4
    )
    recovered = backup.recoverable_configuration_snapshot(
        backup.export_configuration_snapshot(subentry.data)
    )
    assert recovered["functions"] != "[]\n"
    assert len(agent_config.configured_function_tools_from_data(recovered)) == 4
    private = backup.private_configuration_snapshot(subentry.data)
    assert isinstance(private["functions"], str)
    assert len(agent_config.configured_function_tools_from_data(private)) == 4
    # Private recovery snapshots can be persisted directly without changing the
    # distinction between legacy fallback values and explicitly empty Functions.
    prepared = backup.recoverable_configuration_snapshot(private)
    assert prepared["functions"] != "[]\n"
    assert len(agent_config.configured_function_tools_from_data(prepared)) == 4


@pytest.mark.parametrize("empty", [[], "[]\n"])
def test_explicit_empty_list_is_distinct_from_legacy_blank(empty):
    normalized = agent_config.normalize_agent_config({"functions": empty})
    assert agent_config.configured_function_tools_from_data(normalized) == []
