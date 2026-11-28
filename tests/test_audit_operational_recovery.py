"""Cover observed failures and the recovery boundaries exposed in Management."""

import errno
import json
import logging
from pathlib import Path
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiohttp import ClientConnectionError, ClientPayloadError
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses import (
    knowledge,
    management_setup_health as health,
    management_ui,
    memory,
    provider_errors as provider,
    regex_execution,
    request_rules,
    scheduled_action_summary,
    storage_repairs,
    temporary_memory,
)
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SUBSYSTEM_STATUS_KEY,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    SQLiteDeadlineExceeded,
    SQLiteResultLimitExceeded,
)
from custom_components.extended_openai_conversation_responses.operational_errors import (
    failure_diagnostic,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    StorageRepairRequired,
)
from custom_components.extended_openai_conversation_responses.skill_transactions import (
    recover_transactions,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry


def test_overview_observes_authentication_until_matching_recovery(monkeypatch):
    hass = SimpleNamespace(
        data={
            SUBSYSTEM_STATUS_KEY: {
                ("entry", "assistant"): {
                    "knowledge": {"status": "failed", "error_type": "OSError"}
                }
            }
        }
    )
    entry = SimpleNamespace(
        entry_id="entry",
        data={"api_key": "current-key"},
        runtime_data=None,
        async_start_reauth=Mock(),
    )
    subentry = SimpleNamespace(
        subentry_id="assistant",
        title="Example",
        data={"memory_mode": "manual", "memory_retrieval_mode": "hybrid", "knowledge_enabled": True},
    )
    manager = SimpleNamespace(
        needs_storage_repair=True, hybrid_status=lambda: {"status": "lexical_fallback"}
    )
    monkeypatch.setattr(health, "get_loaded_memory", lambda *_: manager)
    monkeypatch.setattr(health, "get_loaded_knowledge", lambda *_: manager)

    def facts():
        return health.build_setup_health_facts(
            hass,
            entry,
            subentry,
            memory_available=True,
            knowledge_available=True,
            knowledge_source_count=1,
            is_admin=True,
            include_exposed_entities=False,
            include_function_tools=False,
        )

    previous = provider.provider_authentication_snapshot(entry)
    failure = provider.ProviderStreamError("private", status_code=401)
    assert provider.request_reauthentication(hass, entry, failure)
    rejected = facts()
    assert rejected["provider_runtime"]["authentication_rejected"]
    assert (
        rejected["memory"]["repair_required"] and rejected["memory"]["keyword_fallback"]
    )
    assert rejected["knowledge"]["failed"] and rejected["knowledge"]["repair_required"]
    entry.data["api_key"] = "replacement-key"
    assert not facts()["provider_runtime"]["authentication_rejected"]
    assert provider.request_reauthentication(hass, entry, failure)
    provider.clear_provider_authentication_rejection(hass, entry, previous)
    assert facts()["provider_runtime"]["authentication_rejected"]
    provider.clear_provider_authentication_rejection(
        hass, entry, provider.provider_authentication_snapshot(entry)
    )
    assert not facts()["provider_runtime"]["authentication_rejected"]
    manager.needs_storage_repair = False
    assert not facts()["knowledge"]["repair_required"]
    subentry.data["memory_retrieval_mode"] = "lexical"
    assert not facts()["memory"]["keyword_fallback"]


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("store_name", ["knowledge", "temporary"])
async def test_retained_native_null_payload_is_preserved(hass, store_name, legacy):
    if store_name == "knowledge":
        storage = knowledge.HomeAssistantKnowledgeStorage(hass, "entry", "agent")
        manager = knowledge.KnowledgeLibrary(storage)
        store = storage._store
    else:
        store = temporary_memory.TemporaryMemoryStore(
            hass, temporary_memory.STORAGE_VERSION, "temporary-memory-probe"
        )
        manager = temporary_memory.TemporaryMemory(store)
    path = Path(store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    await store.async_save(None)
    if legacy:
        envelope = json.loads(path.read_bytes())
        envelope["version"] = 1
        path.write_text(json.dumps(envelope), encoding="utf-8")
    evidence = path.read_bytes()
    with pytest.raises(StorageRepairRequired):
        await manager.async_initialize()
    with pytest.raises(StorageRepairRequired):
        await store.async_save({})
    assert path.read_bytes() == evidence


async def test_storage_repairs_resolve_and_are_assistant_specific(hass, monkeypatch):
    entry = MockConfigEntry(
        domain=DOMAIN,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": id_,
                "unique_id": None,
                "subentry_id": id_,
            }
            for id_ in ("first", "second")
        ],
    )
    hass.config_entries.async_get_entry.return_value = entry
    registry = issue_registry.async_get(hass)
    monkeypatch.setattr(registry, "async_schedule_save", Mock())

    def issue(store, id_):
        return registry.async_get_issue(
            DOMAIN, f"storage_repair_{store}_{entry.entry_id}_{id_}"
        )

    storage_repairs.set_storage_repair(hass, entry.entry_id, "missing", "memory", True)
    assert issue("memory", "missing") is None
    for id_ in ("first", "second"):
        storage_repairs.set_storage_repair(hass, entry.entry_id, id_, "archive", True)
    assert issue("archive", "first").translation_placeholders["assistant"] == "first"
    storage_repairs.set_storage_repair(hass, entry.entry_id, "first", "archive", True)
    storage_repairs.set_storage_repair(hass, entry.entry_id, "first", "archive", False)
    assert issue("archive", "first") is None
    storage_repairs.set_storage_repair(hass, entry.entry_id, "second", "restore", True)
    assert issue("archive", "second") and issue("restore", "second")
    storage_repairs.remove_storage_repairs(hass, entry.entry_id, "second")
    assert issue("archive", "second") is None and issue("restore", "second") is None


@pytest.mark.parametrize("selected", [[], ["Weather", "Missing"]])
async def test_management_skill_refresh_preserves_selection(monkeypatch, selected):
    manager = SimpleNamespace(
        get_all_skills=lambda: [SimpleNamespace(name="Weather")],
        async_load_skills=AsyncMock(),
    )
    monkeypatch.setattr(
        SkillManager, "async_get_instance", AsyncMock(return_value=manager)
    )
    subentry = SimpleNamespace(
        data={
            "skills": selected.copy(),
            "max_function_calls_per_conversation": 0,
        }
    )
    request = SimpleNamespace(
        hass=object(),
        user_id="admin",
        is_admin=True,
        entry=SimpleNamespace(data={}),
        entry_id="entry",
        subentry_id="agent",
        subentry=subentry,
        message={"action": "skills_catalogue"},
    )
    result = await management_ui.async_configuration_command(request)
    assert result == {
        "available": True,
        "installed": ["Weather"],
        "loader_available": False,
    }
    manager.async_load_skills.assert_not_awaited()
    request.message["action"] = "skills_reload"
    assert (await management_ui.async_configuration_command(request))["installed"] == [
        "Weather"
    ]
    manager.async_load_skills.assert_awaited_once()
    assert subentry.data["skills"] == selected
    manager.async_load_skills.side_effect = OSError("private filesystem path")
    assert await management_ui.async_configuration_command(request) == {
        "available": False
    }
    request.is_admin = False
    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await management_ui.async_configuration_command(request)


@pytest.mark.parametrize(
    ("cause", "category", "guidance"),
    [
        (socket.gaierror(), "dns", "DNS"),
        (ssl.SSLError(), "tls", "certificate"),
        (OSError(errno.ECONNREFUSED, "private"), "connection_refused", "host and port"),
        (httpx.ConnectTimeout("private"), "connect_timeout", "reachability"),
        (httpx.ReadTimeout("private"), "read_timeout", "proxy timeouts"),
        (httpx.ReadError("private"), "interrupted_stream", "streaming support"),
    ],
)
def test_transport_diagnostics_use_specific_bounded_evidence(cause, category, guidance):
    intermediate = RuntimeError("private")
    intermediate.__cause__ = cause
    error = provider.ProviderTransportError("private")
    error.__cause__ = intermediate
    assert provider.provider_error_metadata(error)["root_cause_category"] == category
    assert guidance in provider.provider_log_remediation(error)
    assert "private" not in provider.provider_user_message(error)


def test_provider_remediation_covers_schema_parameter_and_invalid_response():
    error = provider.ProviderStreamError(
        "private", code="unsupported_parameter", status_code=400
    )
    error.body = {"error": {"param": "max_output_tokens"}}
    assert (
        provider.provider_error_metadata(error)["affected_setting"]
        == "Maximum response length"
    )
    assert "Maximum response length" in provider.provider_user_message(error)
    error.body["error"]["param"] = "private field"
    assert "private" not in provider.provider_user_message(error)
    schema = provider.ProviderStreamError(
        "private", code="invalid_json_schema", status_code=400
    )
    assert provider.provider_error_metadata(schema)["code"] == "invalid_schema"
    assert "requested output schema" in provider.provider_user_message(schema)
    with pytest.raises(provider.ProviderStreamError, match="status is malformed"):
        provider.ensure_successful_responses_result({"status": " "})


def test_retained_route_attribution_tracks_the_effective_fields(monkeypatch):
    now = 10.0
    monkeypatch.setattr(request_rules, "monotonic", lambda: now)
    runtime = request_rules.RequestRuleRuntime()
    runtime.set("session", {"model": "routed"}, 1, rule_id="model-rule")
    runtime.set("session", {"reasoning_effort": "high"}, 1, rule_id="reasoning-rule")
    assert runtime.effective_options(
        {"model": "default"}, "session", timeout_minutes=1
    ) == {
        "model": "routed",
        "reasoning_effort": "high",
    }
    assert runtime.effective_routing_sources("session", None) == (
        "model-rule",
        "reasoning-rule",
    )
    assert runtime.effective_routing_sources("session", {"model": "temporary"}) == (
        "reasoning-rule",
    )
    assert (
        runtime.effective_routing_sources(
            "session", {request_rules._REQUEST_RESET_SENTINEL: "1"}
        )
        == ()
    )
    error = RuntimeError()
    provider.annotate_provider_request(
        error,
        "routed",
        "responses",
        "initial",
        None,
        retained_routing_rules=runtime.effective_routing_sources("session", None),
    )
    assert error._eoai_provider_request["retained_routing_rules"] == [
        "model-rule",
        "reasoning-rule",
    ]
    now = 71.0
    assert runtime.effective_options({}, "session", timeout_minutes=1) == {}
    assert runtime.effective_routing_sources("session", None) == ()


@pytest.mark.parametrize(
    ("error", "category", "guidance"),
    [
        (ClientConnectionError("private"), "network_connection", None),
        (ClientPayloadError("private"), "response_payload", None),
        (SQLiteDeadlineExceeded("private"), "backend", "deadline"),
        (SQLiteResultLimitExceeded("private"), "backend", "rows or columns"),
    ],
)
def test_operational_diagnostics_use_type_contracts(error, category, guidance):
    diagnostic = failure_diagnostic(error)
    assert diagnostic.category == category
    assert "private" not in diagnostic.summary()
    if guidance:
        assert guidance in diagnostic.explanation


async def test_speech_preview_and_legacy_journal_report_without_private_data(
    tmp_path, monkeypatch, caplog
):
    journal = tmp_path / ("transaction-" + "a" * 32 + ".json")
    journal.write_text('["private-data"]')
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(HomeAssistantError, match="contain an object"),
    ):
        recover_transactions(tmp_path, tmp_path / "installed")
    assert journal.name in caplog.text and "private-data" not in caplog.text
    assert journal.read_text() == '["private-data"]'
    original = regex_execution._SpeechText(
        "literal", (regex_execution._LiteralSpan(0, 7, "code"),)
    )
    replacement = {
        "text": "new",
        "invalid": [],
        "overflow": False,
        "literal_spans": None,
    }
    monkeypatch.setattr(
        regex_execution, "_async_run_regex_worker", AsyncMock(return_value=replacement)
    )
    warnings = []
    assert (
        await regex_execution._async_apply_speech_replacements(
            original,
            [{"pattern": "literal", "replacement": "new"}],
            warnings=warnings,
            assistant="conversation.example",
        )
        is original
    )
    assert "lost literal spans" in warnings[0]


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (None, [1.0]),
        ([1.0], [1.0, 2.0]),
        ([1.0], [0.0]),
        ([1.0], [float("nan")]),
    ],
)
def test_cosine_preparation_preserves_rejection_of_unusable_vectors(left, right):
    assert (
        memory._prepared_cosine_similarity(memory._prepare_cosine_query(left), right)
        is None
    )


@pytest.mark.parametrize(
    ("section", "action", "result", "committed"),
    [
        ("settings", "update", {"revision": "saved"}, True),
        ("configuration", "update", {"valid": True}, True),
        ("configuration", "import", {"status": "unknown"}, False),
        ("request_rules", "create", {"rule": {"id": "existing"}}, True),
        ("memories", "temporary_add", {"status": "created"}, True),
        ("memories", "update", {"status": "updated"}, True),
        ("knowledge", "update", {"status": "rejected"}, False),
        ("scheduled_actions", "cancel", {"status": "cancelled"}, True),
        ("guest_mode", "disable", {"status": {"active": False}}, True),
        ("overview", "get", {"valid": True}, False),
    ],
)
def test_only_committed_mutations_receive_recovery_acknowledgements(
    section, action, result, committed
):
    assert (
        management_ui._acknowledged_management_write(section, action, result)
        is committed
    )


def test_scheduled_summary_exposes_only_bounded_effective_target_names(monkeypatch):
    summary = scheduled_action_summary
    hass = SimpleNamespace(data={summary.DATA_EXPOSED_ENTITIES: {}})
    monkeypatch.setattr(
        summary.fr,
        "async_get",
        lambda _: SimpleNamespace(
            async_get_floor=lambda _: SimpleNamespace(name="Upper\n floor"),
        ),
    )
    monkeypatch.setattr(
        summary.lr,
        "async_get",
        lambda _: SimpleNamespace(
            async_get_label=lambda _: SimpleNamespace(name=" "),
        ),
    )
    monkeypatch.setattr(
        summary.dr,
        "async_get",
        lambda _: SimpleNamespace(
            async_get=lambda _: SimpleNamespace(name_by_user=None, name="Device"),
        ),
    )
    assert summary._indirect_name(hass, "floor_id", "private-id") == "floor Upper floor"
    assert summary._indirect_name(hass, "label_id", "private-id") is None
    assert summary._indirect_name(hass, "device_id", "private-id") == "device Device"
    assert summary._indirect_name(hass, "invalid", "private-id") is None

    excluded = {"light.one", "light.two", "light.three", "light.four"}
    monkeypatch.setattr(
        summary,
        "resolve_action_entity_ids",
        lambda _hass, _domain, _service, data: (
            set() if summary.CONF_EXCLUDE in data else excluded
        ),
    )
    monkeypatch.setattr(summary, "async_should_expose", lambda *_: True)
    data = {summary.CONF_EXCLUDE: {"entity_id": sorted(excluded)}}
    value = summary._target_descriptions(
        hass, "light", "turn_on", data, lambda names: names
    )
    assert "and other targets" in value[0] and "light.two" not in value[0]
    value = summary._target_descriptions(hass, "light", "turn_on", data, lambda _: [])
    assert value == ["with target exclusions"]
