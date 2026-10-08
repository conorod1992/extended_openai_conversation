"""Exhaust remaining reachable residual branches without forcing invariants."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError


def test_backup_recovery_preserves_absence_of_function_fields(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import backup
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    calls = 0

    def normalize(value):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("broken function config")
        return dict(value)

    monkeypatch.setattr(backup, "agent_config_snapshot", normalize)
    monkeypatch.setattr(backup, "preserve_legacy_guest_policy", lambda raw, value: value)
    monkeypatch.setattr(repair, "function_tools_issue", lambda raw: ([], "broken"))
    monkeypatch.setattr(repair, "safe_function_configuration", lambda raw: {})

    result = backup.export_configuration_snapshot({})

    assert backup.CONF_FUNCTION_TOOLS not in result
    assert backup.CONF_FUNCTION_GROUPS not in result


@pytest.mark.asyncio
async def test_delayed_remove_agent_cancels_matching_waiter(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    manager._records = {
        "remove": SimpleNamespace(entry_id="entry", subentry_id="agent"),
        "keep": SimpleNamespace(entry_id="entry", subentry_id="other"),
    }
    waiter = asyncio.create_task(asyncio.sleep(60))
    manager._tasks["remove"] = waiter

    async def save(records):
        manager._records = records

    manager._async_save_records_transactionally = save

    await manager.async_remove_agent("entry", "agent")
    await asyncio.sleep(0)

    assert waiter.cancelled()
    assert set(manager._records) == {"keep"}
    assert "remove" not in manager._tasks


def test_function_group_quarantine_nonlist_value_passes_to_validator(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import agent_config
    from custom_components.extended_openai_conversation_responses import (
        function_tool_quarantine as quarantine,
    )

    seen = Mock(return_value=[])
    monkeypatch.setattr(agent_config, "validate_function_groups", seen)
    token_all = quarantine._RUNTIME_QUARANTINE_ALL_FUNCTIONS.set(True)
    token_names = quarantine._RUNTIME_QUARANTINED_FUNCTION_NAMES.set(frozenset())
    try:
        quarantine._runtime_validate_function_groups({"unexpected": True}, [])
    finally:
        quarantine._RUNTIME_QUARANTINED_FUNCTION_NAMES.reset(token_names)
        quarantine._RUNTIME_QUARANTINE_ALL_FUNCTIONS.reset(token_all)

    seen.assert_called_once_with({"unexpected": True}, [])


def test_zstd_concatenated_frames_decode_fully() -> None:
    from compression.zstd import ZstdCompressor

    from custom_components.extended_openai_conversation_responses.functions import web

    def frame(data):
        compressor = ZstdCompressor()
        return compressor.compress(data) + compressor.flush()

    encoded = frame(b"first") + frame(b"second")

    assert web._decode_zstd(encoded, 1000) == b"firstsecond"


def test_guest_custom_policy_ignores_nonsequence_groups(hass) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    tools = [
        {
            "spec": {"name": "safe"},
            "function": {"type": "native", "name": "execute_service"},
        }
    ]
    options = {
        guest_mode.CONF_GUEST_FUNCTION_POLICY: "custom",
        guest_mode.CONF_GUEST_ALLOWED_FUNCTION_NAMES: ["safe"],
        guest_mode.CONF_GUEST_ALLOWED_GROUP_IDS: ["group"],
        guest_mode.CONF_FUNCTION_GROUPS: "not-a-sequence-of-groups",
    }

    policy = guest_mode._resolve_exclusion_policy(
        hass, options, tools, exposed_entities=[]
    )

    assert "safe" in policy.configured_tool_names


def test_knowledge_chunk_overlap_without_newline_uses_exact_overlap() -> None:
    from custom_components.extended_openai_conversation_responses import knowledge

    content = "x" * (knowledge.CHUNK_SIZE + 500)
    chunks = knowledge._split_chunks(content)

    assert len(chunks) >= 2
    first_start, first = chunks[0]
    second_start, _ = chunks[1]
    assert first_start == 0
    assert second_start == max(1, len(first) - knowledge.CHUNK_OVERLAP)


@pytest.mark.asyncio
async def test_overview_primary_preserves_known_function_health(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_loading_performance as loading,
    )

    health = {"valid": True}
    monkeypatch.setattr(loading, "peek_function_tool_health", Mock(return_value=health))
    monkeypatch.setattr(
        loading,
        "_agent_snapshot",
        Mock(return_value={"knowledge_source_count": 0}),
    )
    monkeypatch.setattr(loading, "settings_snapshot", Mock(return_value={}))
    monkeypatch.setattr(
        loading,
        "build_setup_health_facts",
        Mock(return_value={"memory": {}, "knowledge": {}, "function_tools": health}),
    )
    entry = SimpleNamespace(entry_id="entry", data={}, runtime_data=object())
    subentry = SimpleNamespace(subentry_id="agent", data={})

    result = await loading.async_overview_primary(hass, entry, subentry, is_admin=True)

    assert result["setup_health"]["function_tools"] == health


@pytest.mark.asyncio
async def test_quiet_hours_disabled_state_reschedules_existing_subscriptions(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        quiet_hours_runtime as quiet,
    )

    manager = quiet.QuietHoursManager(hass)
    manager._initialized = True
    manager._config = quiet.QuietHoursConfig(enabled=False)
    manager._active = None
    manager._unsubscribers = [Mock()]
    manager._async_restore_locked = AsyncMock()
    manager._publish_state = Mock()
    manager._reschedule = Mock()

    await manager.async_reconcile()

    manager._async_restore_locked.assert_awaited_once()
    manager._reschedule.assert_called_once()


@pytest.mark.asyncio
async def test_quiet_hours_restore_discards_malformed_controls(hass) -> None:
    from custom_components.extended_openai_conversation_responses import (
        quiet_hours_runtime as quiet,
    )

    manager = quiet.QuietHoursManager(hass)
    manager._active = {
        "controls": {
            7: {"kind": "switch"},
            "switch.bad": "not-a-control",
        }
    }
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked()

    assert manager._active is None
    manager._async_save_locked.assert_awaited_once()


def test_sentence_pattern_whitespace_is_normalized() -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rule_patterns,
    )

    compiled = request_rule_patterns.compile_sentence_pattern("turn   on   {room}")

    assert compiled is not None


def test_request_rule_outcome_probe_preserves_scalar_sequence_members() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    actions, _completed, _stopped = request_rules._outcome_probes(
        [{"sequence": ["literal", {"delay": 1}]}]
    )

    nested = actions[1]["sequence"]
    assert nested[0] == "literal"
    assert nested[1] == {"delay": 1}


def test_restore_runtime_clears_registry_only_managers(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        continuity,
        function_groups,
        request_rules,
        restore_recovery,
    )

    key = ("entry", "agent")
    cont = SimpleNamespace(
        _sessions={"x": 1},
        _memory_bundles={"x": 1},
        _pending_ends={"x"},
        _ignored_conversation_ids={"x": None},
    )
    rules = SimpleNamespace(_conversation_overrides={"x": 1})
    groups = SimpleNamespace(_sessions={"x": 1}, _last_request={"x": 1})
    hass.data[continuity._MANAGERS] = {key: cont}
    hass.data[request_rules._RUNTIMES] = {key: rules}
    hass.data[function_groups._RUNTIMES] = {key: groups}
    monkeypatch.setattr(restore_recovery, "_active_agent", Mock(return_value=None))

    restore_recovery.reset_restored_runtime(hass, "entry", "agent")

    assert cont._sessions == {}
    assert cont._memory_bundles == {}
    assert rules._conversation_overrides == {}
    assert groups._sessions == {}
    assert groups._last_request == {}


def test_skill_recovery_detects_missing_known_good_target(tmp_path) -> None:
    from custom_components.extended_openai_conversation_responses import (
        skill_transactions,
    )

    root = tmp_path / "staging"
    installed = tmp_path / "installed"
    root.mkdir()
    installed.mkdir()
    target = installed / "demo"
    target.mkdir()
    backup = root / f"demo.remove-{'c' * 32}"
    journal = skill_transactions.prepare_transaction(root, target, backup, None)
    target.rename(tmp_path / "moved-away")

    with pytest.raises(HomeAssistantError, match="Known-good Skill is unavailable"):
        skill_transactions.recover_transaction(journal, installed)


def test_skill_publish_failure_without_existing_target_needs_no_restore(
    tmp_path, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses.skills import (
        SkillManager,
    )

    staged = tmp_path / "staged"
    staged.mkdir()
    target = tmp_path / "installed" / "demo"
    backup = tmp_path / "backup"

    real_rename = type(staged).rename

    def rename(self, destination):
        if self == staged:
            raise OSError("publish failed")
        return real_rename(self, destination)

    monkeypatch.setattr(type(staged), "rename", rename)

    with pytest.raises(OSError, match="publish failed"):
        SkillManager._activate_staged_skill_sync(staged, target, backup)

    assert not target.exists()
    assert not backup.exists()


def test_speech_final_link_buffer_flush_directly() -> None:
    from custom_components.extended_openai_conversation_responses import speech

    sanitizer = speech.StreamingSpeechSanitizer()
    sanitizer._buffer = "unfinished"

    assert sanitizer._drain_links(final=True) == "unfinished"
    assert sanitizer._buffer == ""


def test_speech_final_format_buffer_flush_directly() -> None:
    from custom_components.extended_openai_conversation_responses import speech

    sanitizer = speech.StreamingSpeechSanitizer()
    sanitizer._format_buffer = "`unfinished"

    output = sanitizer._format("", final=True)

    assert output == "unfinished"
    assert sanitizer._format_buffer == ""


def test_transfer_configuration_user_collection_ignores_nonmapping_device_mappings() -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    prepared = SimpleNamespace(
        memories=[],
        temporary_memories=[],
        archive_sessions=[],
        config={
            transfer.CONF_VOICE_DEFAULT_USER_ID: "default-user",
            transfer.CONF_VOICE_DEVICE_MAPPINGS: ["bad"],
        },
    )

    result = transfer.transfer_user_scope_ids(
        prepared, [transfer.SECTION_CONFIGURATION]
    )

    assert result == frozenset({"default-user"})


def test_transfer_owner_mapping_leaves_unknown_owner_unchanged() -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    assert transfer._map_owner_value("user:source", {"other": "target"}) == "user:source"


def test_transfer_temporary_scope_mapping_preserves_nonuser_scope() -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    from custom_components.extended_openai_conversation_responses.temporary_memory import (
        TemporaryMemoryRecord,
    )

    record = TemporaryMemoryRecord(
        memory_id="m1",
        scope_id="conversation:abc",
        content="fact",
        category="general",
        source="test",
        expires_at="2099-01-01T00:00:00+00:00",
        created_at="2026-10-06T00:00:00+00:00",
        updated_at="2026-10-06T00:00:00+00:00",
        owner_scope_id="user:source",
    )
    prepared = SimpleNamespace(
        config=None,
        raw_configuration=None,
        memories=None,
        temporary_memories=[record],
        archive_sessions=None,
    )

    mapped = transfer.apply_user_scope_mappings(
        prepared,
        [transfer.SECTION_TEMPORARY_MEMORY],
        {"source": "target"},
    )

    assert mapped.temporary_memories[0].owner_scope_id == "user:target"
    assert mapped.temporary_memories[0].scope_id == "conversation:abc"


@pytest.mark.asyncio
async def test_usage_prune_old_done_callback_does_not_clear_new_task(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import usage

    storage = SimpleNamespace()
    manager = usage.UsageManager(storage)
    manager._last_prune_date = None
    manager.async_prune_details = AsyncMock()

    callbacks = []

    class FakeTask:
        def done(self):
            return False

        def add_done_callback(self, callback):
            callbacks.append(callback)

    fake = FakeTask()

    def create_task(coro, **kwargs):
        coro.close()
        return fake

    monkeypatch.setattr(usage.asyncio, "create_task", create_task)

    await manager._async_prune_usage_if_due()
    replacement = object()
    manager._prune_task = replacement
    callbacks[0](fake)

    assert manager._prune_task is replacement


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mapping", "expected"),
    [
        ("shared", frozenset()),
        ("unretained", frozenset()),
        ("user:missing", frozenset({"default"})),
    ],
)
async def test_voice_identity_mapping_fallback_matrix(mapping, expected) -> None:
    from custom_components.extended_openai_conversation_responses import (
        voice_identity_runtime as voice,
    )

    async def get_user(user_id):
        if user_id == "default":
            return SimpleNamespace(is_active=True)
        return None

    agent = SimpleNamespace(
        hass=SimpleNamespace(
            auth=SimpleNamespace(async_get_user=AsyncMock(side_effect=get_user))
        ),
        subentry=SimpleNamespace(
            data={
                voice.CONF_VOICE_SCOPE_POLICY: voice.VOICE_POLICY_DEVICE_MAPPING,
                voice.CONF_VOICE_DEVICE_MAPPINGS: {"device": mapping},
                voice.CONF_VOICE_UNMAPPED_POLICY: voice.VOICE_POLICY_DEFAULT_USER,
                voice.CONF_VOICE_DEFAULT_USER_ID: "default",
            }
        ),
    )
    user_input = SimpleNamespace(context=None, device_id="device", satellite_id=None)

    assert await voice._active_configured_users(agent, user_input) == expected


def test_request_chat_completions_nonstreaming_omits_stream_options(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import request
    from custom_components.extended_openai_conversation_responses.const import (
        API_MODE_CHAT_COMPLETIONS,
        CONF_API_MODE,
        CONF_CHAT_MODEL,
    )

    capabilities = {
        "streaming": False,
        "reasoning": {"supported": False},
        "structured_outputs": False,
        "service_tiers": [],
    }

    @contextmanager
    def snapshot(_model, _capabilities):
        yield

    monkeypatch.setattr(request, "model_capability_snapshot", snapshot)
    monkeypatch.setattr(
        request, "select_api_path", Mock(return_value=API_MODE_CHAT_COMPLETIONS)
    )
    monkeypatch.setattr(request, "normalize_output_token_limit", Mock(return_value=None))
    monkeypatch.setattr(request, "_sampling_value", Mock(return_value=None))
    monkeypatch.setattr(request, "resolve_effective_capabilities", Mock(return_value={}))

    result = request.build_provider_request_snapshot(
        {
            CONF_CHAT_MODEL: "model",
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
        },
        {},
        tools_required=False,
        model_capabilities=capabilities,
    )

    assert result.api_kwargs["stream"] is False
    assert "stream_options" not in result.api_kwargs
