"""Error UX and payload-free handled-failure diagnostics through public paths."""

from __future__ import annotations

import errno
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bs4 import BeautifulSoup
from openai import OpenAIError
import pytest

from custom_components.extended_openai_conversation_responses import (
    config_flow,
    conversation,
    diagnostics,
    provider_credentials,
    quiet_hours_runtime,
    request_rules,
    restore_recovery,
    usage,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
)
from custom_components.extended_openai_conversation_responses.debug import DebugManager
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    FunctionLoadFailed,
    TokenLengthExceededError,
)
from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
    GuestModeDenied,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    bind_active_ha_context,
    entity_access_error,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    make_tool_result_content,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    CURRENT_SCHEMA_VERSION,
)
from custom_components.extended_openai_conversation_responses.model_catalog_manager import (
    ModelCatalogManager,
)
from custom_components.extended_openai_conversation_responses.operational_errors import (
    function_tool_log_context,
    log_handled_failure,
    storage_failure_reason,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
    log_provider_failure,
    provider_failure_category,
    provider_log_remediation,
)
from custom_components.extended_openai_conversation_responses.request_diagnostics import (
    record_tool_execution,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from tests.test_debug_ui import _call_websocket, _Connection
from tests.test_responses_api import FakeStream, _event
from tests.test_usage_accounting_recovery import FakeStorage

SECRET = "PRIVATE-CANARY-credential-prompt-result"


def test_response_limit_names_the_setting() -> None:
    message = str(TokenLengthExceededError(4096))
    assert "Maximum response length (4096 tokens)" in message
    assert "assistant's Extended OpenAI configuration" in message
    assert "shorter response" in message


@pytest.mark.parametrize(
    "number, guidance",
    [
        (errno.ENOSPC, "free disk space"),
        (errno.EACCES, "permissions"),
        (errno.EROFS, "read-only"),
    ],
)
async def test_private_storage_errno_guidance_does_not_leak(
    hass, monkeypatch, number, guidance
) -> None:
    from custom_components.extended_openai_conversation_responses.strict_store import (
        PropagatingWriteStore,
    )

    async def fail(*_args):
        raise WriteError(SECRET) from OSError(number, SECRET, "/private/path")

    monkeypatch.setattr(Store, "_async_write_data", fail)
    store = PropagatingWriteStore(hass, 1, "test")
    with pytest.raises(OSError) as caught:
        await store._async_write_data({"content": SECRET})
    assert guidance in str(caught.value)
    assert "state change could not be persisted" in str(caught.value)
    assert caught.value.errno == number
    assert SECRET not in str(caught.value)
    assert "/private/path" not in str(caught.value)
    assert storage_failure_reason(None) == "storage operation failed"


def test_entity_exposure_permission_and_filtered_policy_are_distinct(
    hass, monkeypatch
) -> None:
    from homeassistant.components.homeassistant import exposed_entities

    hass.data[exposed_entities.DATA_EXPOSED_ENTITIES] = object()
    monkeypatch.setattr(exposed_entities, "async_should_expose", lambda *_: False)
    with bind_active_ha_context(None):
        message = str(entity_access_error(hass, ["light.test"]))
        assert "Voice assistants > Expose" in message
    with bind_active_ha_context(Context(user_id="restricted")):
        message = str(entity_access_error(hass, ["light.test"]))
        assert "user permissions" in message
        assert "Voice assistants" not in message
        assert "light.test" not in message
    monkeypatch.setattr(exposed_entities, "async_should_expose", lambda *_: True)
    with bind_active_ha_context(None):
        message = str(entity_access_error(hass, ["light.test"]))
        assert "access policy" in message
        assert "Voice assistants" not in message


def test_live_guest_denial_does_not_advise_assist_exposure(hass, monkeypatch) -> None:
    agent = conversation.ExtendedOpenAIAgentEntity.__new__(
        conversation.ExtendedOpenAIAgentEntity
    )
    agent.hass = hass
    agent._effective_guest_policy = lambda: GuestCapabilityPolicy(
        True, controllable_entity_ids=frozenset()
    )
    monkeypatch.setattr(
        conversation, "get_exposed_entities", lambda *_: [{"entity_id": "light.test"}]
    )
    with pytest.raises(GuestModeDenied) as caught:
        agent._require_current_action_targets(hass, {"light.test"})
    assert "Guest Mode" in str(caught.value)
    assert "Expose" not in str(caught.value)


def test_live_action_targets_reject_changed_context_and_removed_entities(
    hass, monkeypatch
) -> None:
    agent = conversation.ExtendedOpenAIAgentEntity.__new__(
        conversation.ExtendedOpenAIAgentEntity
    )
    agent.hass = hass
    agent._effective_guest_policy = lambda: GuestCapabilityPolicy(False)
    monkeypatch.setattr(conversation, "get_exposed_entities", lambda *_: [])
    monkeypatch.setattr(hass.states, "get", lambda _entity_id: None)

    agent.hass = object()
    with pytest.raises(HomeAssistantError, match="context changed"):
        agent._require_current_action_targets(hass, {"light.test"})

    agent.hass = hass
    with pytest.raises(HomeAssistantError, match="no longer exists"):
        agent._require_current_action_targets(hass, {"light.missing"})

    monkeypatch.setattr(
        hass.states,
        "get",
        lambda entity_id: object() if entity_id == "light.test" else None,
    )
    with pytest.raises(HomeAssistantError, match="light.test"):
        agent._require_current_action_targets(hass, {"light.test"})


async def test_usage_fallback_is_explained_and_shared(
    hass, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(
        usage,
        "async_get_durable_usage",
        AsyncMock(side_effect=OSError(errno.ENOSPC, SECRET)),
    )
    manager = await usage.async_get_usage(hass, "entry", "assistant")
    assert await usage.async_get_usage(hass, "entry", "assistant") is manager
    assert manager.persistence_status() == {
        "mode": "volatile",
        "survives_restart": False,
    }
    assert "lost on Home Assistant restart" in caplog.text
    assert (
        caplog.text.count("continuing with volatile accounting") == 2
    )  # warning + DEBUG stack
    assert SECRET not in caplog.text
    assert usage.UsageManager(FakeStorage()).persistence_status()["mode"] == "durable"


@pytest.mark.parametrize(
    "code, category, guidance",
    [
        ("model_not_found", "model_unavailable", "selected model"),
        ("DeploymentNotFound", "model_unavailable", "deployment name"),
        ("insufficient_quota", "insufficient_quota", "billing"),
        ("context_length_exceeded", "context_length", "context limit"),
        ("unsupported_parameter", "unsupported_parameter", "advanced settings"),
    ],
)
def test_specific_provider_evidence_guides_user(code, category, guidance) -> None:
    error = ProviderStreamError(SECRET, code=code)
    assert provider_failure_category(error) == category
    assert guidance in provider_log_remediation(error)
    generic = ProviderStreamError("Unknown", status_code=404)
    assert provider_failure_category(generic) != "model_unavailable"


async def test_memory_fallback_records_cause_once_and_recovery(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    provider = AsyncMock(
        side_effect=ProviderStreamError(SECRET, code="insufficient_quota")
    )
    memory.set_embedding_provider(provider, "embedding-model")
    for _ in range(3):
        assert await memory.async_prepare_hybrid(["scope"], SECRET) is None
    assert memory.stats()["hybrid_retrieval"]["reason"] == "insufficient_quota"
    assert "semantic matches may be missed" in caplog.text
    assert "billing" in caplog.text
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert SECRET not in caplog.text
    provider.side_effect = None
    provider.return_value = [[1.0, 0.0]]
    assert await memory.async_prepare_hybrid(["scope"], SECRET) == [1.0, 0.0]
    assert "semantic retrieval recovered" in caplog.text


async def test_scraper_identifies_extraction_without_url_or_content(
    hass, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    scraper = web.ScrapeFunction()
    soup = BeautifulSoup(f"<a>{SECRET}</a>", "html.parser")
    with function_tool_log_context("page_lookup"):
        assert (
            scraper._extract_value(soup, {"select": "a", "attribute": "href"}, 2)
            is None
        )
        assert scraper._extract_value(soup, {"select": "a", "index": 3}, 4) is None
        assert scraper._extract_value(soup, {"select": "a"}) == SECRET
    assert "page_lookup extraction=2: attribute=href" in caplog.text
    assert "extraction=4: index=3 unavailable; matches=1" in caplog.text
    assert SECRET not in caplog.text


def test_tool_assembly_internal_failure_has_safe_stack_and_no_yaml_blame(
    hass, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    agent = conversation.ExtendedOpenAIAgentEntity.__new__(
        conversation.ExtendedOpenAIAgentEntity
    )
    agent.hass = hass
    agent.entry = SimpleNamespace(entry_id="entry")
    agent.subentry = SimpleNamespace(subentry_id="assistant")
    agent._get_configured_function_tools = Mock(side_effect=TypeError(SECRET))
    with pytest.raises(FunctionLoadFailed) as caught:
        agent._get_function_tools()
    assert "yaml" not in str(caught.value).lower()
    assert "TypeError" in caplog.text and "_get_function_tools" in caplog.text
    assert SECRET not in caplog.text
    agent._get_configured_function_tools.side_effect = AgentConfigError(
        "functions[1].spec.name", "invalid identifier"
    )
    with pytest.raises(AgentConfigError, match=r"functions\[1\].spec.name"):
        agent._get_function_tools()


async def test_restore_rollback_retains_original_cause(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    rollback = AsyncMock()
    monkeypatch.setattr(restore_recovery, "_rollback_transaction", rollback)
    try:
        raise RuntimeError(SECRET)
    except RuntimeError as err:
        with pytest.raises(
            HomeAssistantError, match="previous agent state was recovered"
        ):
            await restore_recovery._recover_failed_apply(
                None,
                SimpleNamespace(entry_id="entry"),
                SimpleNamespace(subentry_id="assistant"),
                (),
                None,
                None,
                err,
            )
    rollback.assert_awaited_once()
    assert "rollback=recovered" in caplog.text
    assert (
        "RuntimeError" in caplog.text
        and "test_restore_rollback_retains_original_cause" in caplog.text
    )
    assert SECRET not in caplog.text


@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_quiet_hours_apply_and_restore_failures_retain_context(
    hass, monkeypatch, caplog, kind
) -> None:
    caplog.set_level(logging.DEBUG)
    manager = quiet_hours_runtime.QuietHoursManager(hass)
    manager._async_save_locked = AsyncMock()
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError(SECRET))
    manager._async_set_switch = AsyncMock(side_effect=RuntimeError(SECRET))
    monkeypatch.setattr(quiet_hours_runtime, "_current_volume", lambda *_: 0.8)
    monkeypatch.setattr(quiet_hours_runtime, "_current_switch", lambda *_: True)
    controls = {}
    if kind == "volume":
        await manager._async_apply_volume_locked(
            "assist_satellite.room", "media_player.room", controls
        )
        entity_id, original, quiet = "media_player.room", 1.0, 0.8
    else:
        await manager._async_apply_switch_locked(
            "assist_satellite.room", "switch.wake", False, controls
        )
        entity_id, original, quiet = "switch.wake", False, True
    assert controls == {}
    manager._active = {
        "controls": {
            entity_id: {"kind": kind, "original_value": original, "quiet_value": quiet}
        }
    }
    await manager._async_restore_locked()
    assert manager._active["controls"][entity_id] == {
        "kind": kind, "original_value": original, "quiet_value": quiet,
        "restoration_pending": True,
    }
    assert "ownership=retained" in caplog.text
    assert "operation=apply" in caplog.text and "operation=restore" in caplog.text
    assert f"control={kind}" in caplog.text and "RuntimeError" in caplog.text
    assert SECRET not in caplog.text


async def test_catalogue_failure_records_cause_and_keeps_data(hass, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    manager = ModelCatalogManager(hass)
    manager.store = SimpleNamespace(async_save=AsyncMock())
    await manager._record_failed_check(123, transient=True, error=TimeoutError(SECRET))
    assert "operation=refresh category=transient" in caplog.text
    assert "TimeoutError" in caplog.text
    assert "current catalogue was kept" in manager.last_error
    assert SECRET not in caplog.text


def test_invalid_yaml_quarantine_does_not_log_private_configuration(caplog) -> None:
    from contextvars import copy_context

    from custom_components.extended_openai_conversation_responses.function_tool_quarantine import (
        _runtime_configured_function_tools,
    )

    caplog.set_level(logging.DEBUG)
    assert (
        copy_context().run(
            _runtime_configured_function_tools, {"functions": f"[{SECRET}"}
        )
        == []
    )
    assert "Functions to repair" in caplog.text
    assert "field=functions" in caplog.text
    assert SECRET not in caplog.text


async def test_catalogue_actual_refresh_retains_failure_category(
    hass, monkeypatch, caplog
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_catalog_manager,
    )

    caplog.set_level(logging.DEBUG)
    # Use the real manager operation with a controlled failing transport.
    from unittest.mock import MagicMock

    response = MagicMock()
    response.__aenter__ = AsyncMock(side_effect=TimeoutError(SECRET))
    session = SimpleNamespace(get=Mock(return_value=response))
    monkeypatch.setattr(
        model_catalog_manager, "async_get_clientsession", lambda *_: session
    )
    manager = ModelCatalogManager(hass)
    manager.store = SimpleNamespace(async_save=AsyncMock())
    await manager.async_check(force=True)
    assert "operation=refresh category=transient" in caplog.text
    assert "TimeoutError" in caplog.text
    assert SECRET not in caplog.text


async def test_startup_passes_safe_guidance_to_ha_retry(
    hass, monkeypatch, caplog
) -> None:
    from custom_components import extended_openai_conversation_responses as integration
    from homeassistant.exceptions import ConfigEntryNotReady

    error = ProviderStreamError(
        SECRET, code="insufficient_quota", request_id="req_safe"
    )
    monkeypatch.setattr(
        integration, "get_authenticated_client", AsyncMock(side_effect=error)
    )
    with pytest.raises(ConfigEntryNotReady) as caught:
        await integration.async_setup_entry(
            hass, SimpleNamespace(entry_id="entry", data={"api_key": SECRET})
        )
    assert "billing" in str(caught.value)
    assert SECRET not in str(caught.value) and SECRET not in caplog.text
    assert "req_safe" in caplog.text


@pytest.mark.parametrize("error", [RuntimeError(SECRET), OpenAIError(SECRET)])
async def test_candidate_key_cannot_leak_from_validation(
    hass, monkeypatch, caplog, error
) -> None:
    caplog.set_level(logging.DEBUG)
    # Even an arbitrary compatible-provider credential echoed in structured fields
    # or an exception cause must not make it to the logging formatter.
    error.code = SECRET
    error.request_id = SECRET
    error.__cause__ = ValueError(SECRET)
    monkeypatch.setattr(
        provider_credentials, "get_authenticated_client", AsyncMock(side_effect=error)
    )
    entry = SimpleNamespace(entry_id="entry", data={"api_key": "previous"})
    with pytest.raises(HomeAssistantError, match="existing API key was not changed"):
        await provider_credentials.async_replace_api_key(hass, entry, SECRET)
    assert SECRET not in caplog.text
    assert entry.data["api_key"] == "previous"
    assert "validation" in caplog.text


async def test_config_flow_unexpected_validation_never_logs_candidate(
    hass, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(
        config_flow, "validate_input", AsyncMock(side_effect=RuntimeError(SECRET))
    )
    flow = config_flow.ExtendedOpenAIConversationConfigFlow()
    flow.hass = hass
    flow.async_show_form = Mock(return_value={})
    await flow._async_finish_initial_setup({"api_key": SECRET}, "user", None)
    assert "stage=setup" in caplog.text and "RuntimeError" in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize("response", ["{missing}", "{lookup.missing}"])
async def test_rule_final_response_failure_does_not_rerun_successful_actions(
    hass, monkeypatch, caplog, response
) -> None:
    rule = {
        "id": "rule",
        "name": "Lookup",
        "action_type": "local_action",
        "action": {
            "actions": [{"variables": {"ok": True}}],
            "success_response": response,
            "failure_response": "Actions failed",
            "continue_to_ai": False,
        },
    }
    marker = "completed"
    monkeypatch.setattr(
        request_rules, "_outcome_probes", lambda actions: (actions, marker, "stopped")
    )
    monkeypatch.setattr(
        request_rules,
        "async_validate_actions_config",
        AsyncMock(side_effect=lambda _hass, value: value),
    )
    script = SimpleNamespace(
        async_run=AsyncMock(
            return_value=SimpleNamespace(
                variables={marker: True}, conversation_response=None
            )
        ),
        async_unload=AsyncMock(),
    )

    async def run(*_args):
        request_rules._ACTIVE_FUNCTION_RESULTS.get()["lookup"] = {"available": SECRET}
        return SimpleNamespace(variables={marker: True}, conversation_response=None)

    script.async_run.side_effect = run
    monkeypatch.setattr(request_rules, "Script", Mock(return_value=script))
    result = await request_rules._async_evaluate_matched_rule(
        hass,
        request_rules.RuleMatch(rule, "test", False, 1.0),
        "test",
        request_rules.RequestRuleRuntime(),
        "session",
        "model",
        None,
        10,
        None,
        None,
    )
    script.async_run.assert_awaited_once()
    assert not result.successful
    assert (
        "action execution succeeded, but response construction failed"
        in result.response
    )
    assert "Success response/template" in result.response
    assert "missing" in result.response
    assert "Actions failed" not in result.response
    assert "Lookup" in caplog.text


async def test_actual_request_debug_endpoint_preserves_summaries_and_bounds(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import debug_ui
    from custom_components.extended_openai_conversation_responses.management_result_limits import (
        MANAGEMENT_DEBUG_PAGE_CHARACTERS,
    )

    manager = DebugManager()
    manager.configure(enabled=True, limit=10)
    trace = manager.begin(
        entry_id="entry",
        subentry_id="assistant",
        user_input={},
        incoming_conversation_id=None,
    )
    trace.system_prompt = "p" * 500_000
    trace.phases_ms = {"tools": 40, "prepare": 10}
    trace.memory = {
        "_payload_preparation": {
            "function_tool_assembly": {"calls": 1, "total_ms": 10}
        },
        "_payload_tool_calls": [{"name": "lookup", "duration_ms": 40}],
        "retrieved": "x" * 500_000,
    }
    first = trace.start_provider_request("responses", (), {"input": "x" * 500_000})
    first.metrics.update(
        {
            "tool_breakdown": [
                {"name": "lookup", "characters": 100, "approx_tokens": 25}
            ],
            "input_breakdown": {
                "by_kind": {"user": {"characters": 200, "approx_tokens": 50}}
            },
        }
    )
    first.usage.update({"input_tokens": 100, "cached_input_tokens": 50})
    for _ in range(6):
        trace.start_provider_request("embeddings", (), {"input": "x" * 500_000})
    manager.finish(trace, successful=True)
    # Endpoint must not serialize the complete capture or non-page provider bodies.
    monkeypatch.setattr(
        type(trace),
        "as_dict",
        Mock(side_effect=AssertionError("unbounded serialization")),
    )
    monkeypatch.setattr(debug_ui, "_manager", lambda *_: manager)
    connection = _Connection()
    for offset in (0, 2):
        await _call_websocket(
            SimpleNamespace(),
            connection,
            {
                "id": 1,
                "action": "get",
                "debug_id": trace.debug_id,
                "provider_offset": offset,
                "provider_limit": 2,
            },
        )
        page = connection.results[-1][1]["trace"]
        summary = page["payload_latency_diagnostics"]
        assert (
            summary["model_request_count"] == 1
            and summary["embedding_request_count"] == 6
        )
        assert summary["preparation"]["function_tool_assembly"]["calls"] == 1
        assert summary["function_tool_calls"][0]["name"] == "lookup"
        assert summary["slowest_phases"][0] == {"name": "tools", "duration_ms": 40}
        assert summary["largest_first_model_request_contributors"]
        assert summary["provider_reported_model_cache_ratio"] == 0.5
        assert len(page["provider_requests"]) == 2
        assert (
            page["management_projection"]["provider_requests"]["next_offset"]
            == offset + 2
        )
        assert len(json.dumps(page)) < MANAGEMENT_DEBUG_PAGE_CHARACTERS + 100_000
    await _call_websocket(SimpleNamespace(), connection, {"id": 2, "action": "runs"})
    row = connection.results[-1][1]["runs"][0]
    assert row["model_request_count"] == 1
    assert row["provider_reported_model_cache_ratio"] == 0.5


@pytest.mark.parametrize(
    "value, outcome",
    [
        ({"error": "failure"}, "failed"),
        ({"status": "denied"}, "denied"),
        ({"exit_code": 1}, "failed"),
        ({"result": "ok"}, "returned"),
    ],
)
def test_tool_handler_return_is_not_proof_of_action_success(value, outcome) -> None:
    import time

    trace = SimpleNamespace(memory={})
    result = make_tool_result_content(
        agent_id="agent",
        tool_call_id="call",
        tool_name="tool",
        tool_result={"result": json.dumps(value)},
    )
    record_tool_execution(
        trace, {}, SimpleNamespace(tool_name="tool"), time.monotonic(), True, result
    )
    attempt = trace.memory["_payload_tool_calls"][0]
    assert attempt["handler_returned"] is True
    assert attempt["outcome"] == outcome
    assert attempt["successful"] is (outcome == "returned")


async def test_provider_stream_and_operational_logs_do_not_capture_private_text(
    caplog,
) -> None:
    caplog.set_level(logging.DEBUG)
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    stream = FakeStream(
        [
            _event("response.output_text.delta", delta=SECRET),
            _event("response.completed", response=SimpleNamespace(usage=None)),
        ]
    )
    deltas = [
        delta
        async for delta in entity._transform_responses_stream(SimpleNamespace(), stream)
    ]
    assert any(delta.get("content") == SECRET for delta in deltas)
    log_provider_failure(
        logging.getLogger(__name__), "Provider failed", OpenAIError(SECRET)
    )
    try:
        raise RuntimeError(SECRET)
    except RuntimeError as err:
        log_handled_failure(logging.getLogger(__name__), "Handled operation", err)
    assert SECRET not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_downloadable_diagnostics_allowlist(hass) -> None:
    subentry = SimpleNamespace(
        subentry_type="conversation",
        subentry_id="assistant",
        data={
            "chat_model": "gpt-6-astra",
            "api_mode": "auto",
            "max_tokens": 1000,
            "prompt": SECRET,
            "functions": "[]",
        },
    )
    entry = SimpleNamespace(
        entry_id="entry",
        data={
            "api_key": SECRET,
            "base_url": f"https://user:{SECRET}@private.invalid/v1?token={SECRET}",
        },
        subentries={"assistant": subentry},
    )
    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    assert result["provider_category"] == "compatible"
    assert result["model_catalogue"]["schema_version"] == CURRENT_SCHEMA_VERSION
    assert result["openai_sdk_version"]
    agent = result["conversation_agents"][0]
    assert agent["selected_model"] == "gpt-6-astra"
    assert agent["configured_api_mode"] == "auto"
    assert agent["effective_api_mode"] == "responses"
    assert agent["provider_request_flags"]["effective_response_limit"] == 1000
    assert agent["usage_persistence"]["mode"] == "durable"
    serialized = json.dumps(result)
    assert SECRET not in serialized and "private.invalid" not in serialized
    assert '"prompt"' not in serialized and '"functions"' not in serialized
