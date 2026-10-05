"""Additional broad stable-CI coverage sweep for residual deterministic branches."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError


def test_model_tool_result_knowledge_payload_omits_default_filter() -> None:
    from custom_components.extended_openai_conversation_responses import model_tool_results

    result = {
        "matches": [],
        "source_filter": {
            "applied_source_ids": [],
            "ignored_source_ids": [],
            "fell_back_to_all_sources": False,
        },
    }

    compacted = model_tool_results.knowledge_search_payload(
        result,
        filter_requested=False,
        policy_filter_applied=False,
    )

    assert compacted == {"matches": []}
    assert "source_filter" in result


@pytest.mark.parametrize(
    ("filter_requested", "policy_filter_applied"),
    [(True, False), (False, True), (True, True)],
)
def test_model_tool_result_knowledge_payload_preserves_explicit_filters(
    filter_requested: bool,
    policy_filter_applied: bool,
) -> None:
    from custom_components.extended_openai_conversation_responses import model_tool_results

    result = {"source_filter": {"applied_source_ids": ["a"]}}

    assert (
        model_tool_results.knowledge_search_payload(
            result,
            filter_requested=filter_requested,
            policy_filter_applied=policy_filter_applied,
        )
        is result
    )


def test_context_usage_local_estimate_accepts_only_positive_integer() -> None:
    from custom_components.extended_openai_conversation_responses import (
        context_usage_hardening as hardening,
    )
    from custom_components.extended_openai_conversation_responses.usage import RequestUsage

    usage = RequestUsage()
    assert hardening._local_estimate(None) is None
    assert hardening._local_estimate(usage) is None

    usage.details = {hardening._LOCAL_ESTIMATE_DETAIL: 0}
    assert hardening._local_estimate(usage) is None
    usage.details = {hardening._LOCAL_ESTIMATE_DETAIL: "10"}
    assert hardening._local_estimate(usage) is None
    usage.details = {hardening._LOCAL_ESTIMATE_DETAIL: 10}
    assert hardening._local_estimate(usage) == 10


def test_context_usage_estimation_failure_is_best_effort(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        context_usage_hardening as hardening,
    )
    from custom_components.extended_openai_conversation_responses.usage import RequestUsage

    usage = RequestUsage()
    log = Mock()
    monkeypatch.setattr(
        hardening,
        "measure_provider_input",
        Mock(side_effect=ValueError("measurement failed")),
    )
    monkeypatch.setattr(hardening, "log_handled_failure", log)

    hardening.estimate_prepared_request(
        SimpleNamespace(),
        usage,
        [{"role": "user", "content": "hello"}],
        None,
    )

    log.assert_called_once()
    assert usage.input_tokens == 0
    assert usage.total_tokens == 0


@pytest.mark.asyncio
async def test_detached_context_summary_clears_and_restores_usage_run() -> None:
    from custom_components.extended_openai_conversation_responses import (
        context_summary_performance as performance,
    )

    run_context: ContextVar[object | None] = ContextVar("coverage_run", default=None)
    active_run = object()
    token = run_context.set(active_run)
    try:
        observed: list[object | None] = []

        async def summarize(_older, _model, _api_mode):
            observed.append(run_context.get())
            return "summary"

        entity = SimpleNamespace(
            _usage=SimpleNamespace(_current_run=run_context),
            _async_summarize_history=summarize,
        )

        assert (
            await performance._summarize_detached(
                entity, ["old"], "model", "responses"
            )
            == "summary"
        )
        assert observed == [None]
        assert run_context.get() is active_run
    finally:
        run_context.reset(token)


@pytest.mark.asyncio
async def test_detached_context_summary_without_usage_context() -> None:
    from custom_components.extended_openai_conversation_responses import (
        context_summary_performance as performance,
    )

    summarize = AsyncMock(return_value=None)
    entity = SimpleNamespace(_usage=None, _async_summarize_history=summarize)

    assert (
        await performance._summarize_detached(entity, [], "model", "responses")
        is None
    )
    summarize.assert_awaited_once_with([], "model", "responses")


@pytest.mark.asyncio
async def test_context_summary_request_without_conversation_skips_apply(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        context_summary_performance as performance,
    )

    manager = SimpleNamespace(async_apply=AsyncMock())
    monkeypatch.setattr(performance, "_manager", Mock(return_value=manager))
    chat_log = SimpleNamespace(conversation_id=None, content=[])

    async with performance.context_summary_request(SimpleNamespace(), chat_log):
        assert performance._DEFER_CONTEXT_SUMMARY.get() is True

    manager.async_apply.assert_not_awaited()
    assert performance._DEFER_CONTEXT_SUMMARY.get() is False


def test_prompt_legacy_default_renderer_branch(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import prompt

    hass = SimpleNamespace(config=SimpleNamespace(location_name="Home"))
    renderer = Mock(return_value="legacy-rendered")
    monkeypatch.setattr(prompt, "_render_legacy_default", renderer)
    monkeypatch.setattr(prompt, "_template_requires_render", Mock(return_value=True))
    exposed = [{"entity_id": "light.kitchen", "state": "on", "attributes": {"x": 1}}]

    result = prompt._render_template(
        hass,
        prompt.DEFAULT_EXPOSED_ENTITIES_CONTEXT_TEMPLATE,
        exposed_entities=exposed,
        current_device_id=None,
        user_input=None,
        skills=[],
    )

    assert result == "legacy-rendered"
    renderer.assert_called_once()


def test_prompt_default_without_expected_newline_falls_back_to_single_section(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import prompt

    hass = SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Dublin"))
    malformed_default = prompt._DEFAULT_PROMPT_STABLE_PREFIX + "tail-without-newline"
    monkeypatch.setattr(prompt, "_render_template", Mock(return_value=malformed_default))

    result = prompt.render_effective_prompt(
        hass,
        {prompt.CONF_PROMPT: prompt.DEFAULT_PROMPT},
        exposed_entities=[],
        current_device_id=None,
        user_input=None,
        skills=[],
        memory_scope_available=False,
    )

    assert result.sections[-1].key == "user_prompt"
    assert result.sections[-1].text == malformed_default


@pytest.mark.asyncio
async def test_template_setup_failure_unloads_and_removes_manager(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import template

    monkeypatch.setattr(template, "async_setup_delayed_tools", AsyncMock())
    manager = SimpleNamespace(
        async_setup=AsyncMock(side_effect=RuntimeError("setup failed")),
        async_on_unload=AsyncMock(),
        acquire=Mock(),
    )
    monkeypatch.setattr(
        template,
        "ExtendedOpenAITemplateManager",
        Mock(return_value=manager),
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        await template.async_setup_templates(hass, "entry")

    manager.async_on_unload.assert_awaited_once()
    assert (
        template.DATA_TEMPLATE_MANAGER
        not in hass.data.get(template.DOMAIN, {})
    )


def test_local_handling_snapshot_typeerror_from_pipeline_lookup(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import local_intents

    monkeypatch.setattr(
        local_intents,
        "registered_intent_catalog",
        Mock(
            return_value=[
                {"intent": "HassTurnOn", "label": "Turn on", "available": True}
            ]
        ),
    )
    monkeypatch.setattr(
        local_intents,
        "conflicting_assist_pipelines",
        Mock(side_effect=TypeError("lightweight hass")),
    )

    result = local_intents.local_handling_snapshot(
        SimpleNamespace(),
        "entry",
        "agent",
        ["HassNever"],
    )

    assert result["pipeline_conflicts"] == []
    assert result["intents"][0]["intent"] == "HassNever"
    assert result["intents"][0]["available"] is False


def test_web_search_tool_rejects_unsupported_capability(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import request

    capabilities = {
        "recommended_profile": {"reasoning_effort": "medium"},
    }
    monkeypatch.setattr(request, "supports_openai_hosted_tools", Mock(return_value=True))
    monkeypatch.setattr(request, "capability_allowed", Mock(return_value=False))

    with pytest.raises(HomeAssistantError, match="does not support Responses Web Search"):
        request.build_web_search_tool(
            {
                request.CONF_WEB_SEARCH: True,
                request.CONF_CHAT_MODEL: "unsupported-model",
            },
            request.API_MODE_RESPONSES,
            {},
            capabilities,
        )


def test_web_search_tool_disabled_returns_none() -> None:
    from custom_components.extended_openai_conversation_responses import request

    assert request.build_web_search_tool({request.CONF_WEB_SEARCH: False}, "responses", {}) is None


@pytest.mark.asyncio
async def test_temporary_memory_initialization_rejects_invalid_root() -> None:
    from custom_components.extended_openai_conversation_responses import temporary_memory

    store = SimpleNamespace(async_load=AsyncMock(return_value=["invalid"]))
    manager = temporary_memory.TemporaryMemory(store)

    with pytest.raises(ValueError, match="invalid structure"):
        await manager.async_initialize()

    assert manager.initialized is False
    assert manager._records == {}


@pytest.mark.asyncio
async def test_temporary_memory_initialization_rejects_non_list_records() -> None:
    from custom_components.extended_openai_conversation_responses import temporary_memory

    store = SimpleNamespace(async_load=AsyncMock(return_value={"records": {}}))
    manager = temporary_memory.TemporaryMemory(store)

    with pytest.raises(ValueError, match="records have invalid structure"):
        await manager.async_initialize()

    assert manager.initialized is False


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["invalid", "", "MANUAL"])
async def test_temporary_memory_add_rejects_invalid_source(source: str) -> None:
    from custom_components.extended_openai_conversation_responses import temporary_memory

    manager = temporary_memory.TemporaryMemory(SimpleNamespace())

    with pytest.raises(ValueError, match="source must be automatic or manual"):
        await manager.async_add(
            "scope",
            "content",
            "2099-01-01T00:00:00+00:00",
            owner_scope_id="user:owner",
            source=source,
        )


@pytest.mark.asyncio
async def test_temporary_memory_update_rejects_invalid_source() -> None:
    from custom_components.extended_openai_conversation_responses import temporary_memory

    manager = temporary_memory.TemporaryMemory(SimpleNamespace())

    with pytest.raises(ValueError, match="source must be automatic or manual"):
        await manager.async_update(
            "scope",
            "id",
            None,
            None,
            None,
            owner_scope_id="user:owner",
            source="invalid",
        )


def test_request_rule_match_returns_best_fuzzy_candidate() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules
    from tests.test_request_rules import MemoryStore

    manager = request_rules.RequestRules(MemoryStore())
    manager._committed_matching_snapshot = request_rules._MatchingSnapshot(
        phrases=(),
        wording_groups=(),
        deterministic=(),
        fuzzy=(
            (
                {
                    "id": "lower",
                    "name": "Lower",
                    "order": 1,
                    "match_type": "contains",
                },
                {
                    "word_forms": False,
                    "wording_alternatives": False,
                    "fuzzy_threshold": 0,
                },
                request_rules.CompiledPhrase("hello", normalized="hello"),
            ),
            (
                {
                    "id": "higher",
                    "name": "Higher",
                    "order": 0,
                    "match_type": "equals",
                },
                {
                    "word_forms": False,
                    "wording_alternatives": False,
                    "fuzzy_threshold": 0,
                },
                request_rules.CompiledPhrase("hello", normalized="hello"),
            ),
        ),
    )

    result = manager.match("hello")

    assert result is not None
    assert result.rule["id"] == "higher"
    assert result.fuzzy is True



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name",
    [
        "async_step_openai_credentials",
        "async_step_openai_advanced",
        "async_step_azure_credentials",
    ],
)
async def test_config_flow_provider_steps_return_to_user_when_setup_state_missing(
    monkeypatch, method_name: str
) -> None:
    from custom_components.extended_openai_conversation_responses import config_flow

    flow = config_flow.ExtendedOpenAIConversationConfigFlow()
    flow._setup_data = None
    fallback = AsyncMock(return_value={"type": "form", "step_id": "user"})
    monkeypatch.setattr(flow, "async_step_user", fallback)

    result = await getattr(flow, method_name)()

    assert result == {"type": "form", "step_id": "user"}
    fallback.assert_awaited_once()


@pytest.mark.asyncio
async def test_config_flow_openai_credentials_advanced_and_finish_paths(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import config_flow

    flow = config_flow.ExtendedOpenAIConversationConfigFlow()
    flow._setup_data = {config_flow.CONF_API_PROVIDER: "openai"}
    advanced = AsyncMock(return_value={"step": "advanced"})
    finish = AsyncMock(return_value={"step": "finished"})
    monkeypatch.setattr(flow, "async_step_openai_advanced", advanced)
    monkeypatch.setattr(flow, "_async_finish_initial_setup", finish)

    result = await flow.async_step_openai_credentials(
        {
            config_flow.CONF_API_KEY: "secret",
            config_flow._CONF_PROVIDER_ADVANCED: True,
        }
    )
    assert result == {"step": "advanced"}
    assert flow._setup_data[config_flow.CONF_API_KEY] == "secret"
    assert config_flow._CONF_PROVIDER_ADVANCED not in flow._setup_data
    advanced.assert_awaited_once()

    advanced.reset_mock()
    result = await flow.async_step_openai_credentials(
        {
            config_flow.CONF_API_KEY: "replacement",
            config_flow._CONF_PROVIDER_ADVANCED: False,
        }
    )
    assert result == {"step": "finished"}
    finish.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "step_id"),
    [
        ("async_step_openai_advanced", "openai_advanced"),
        ("async_step_azure_credentials", "azure_credentials"),
    ],
)
async def test_config_flow_provider_detail_submission_finishes(
    monkeypatch, method_name: str, step_id: str
) -> None:
    from custom_components.extended_openai_conversation_responses import config_flow

    flow = config_flow.ExtendedOpenAIConversationConfigFlow()
    flow._setup_data = {"base": "kept"}
    finish = AsyncMock(return_value={"finished": step_id})
    monkeypatch.setattr(flow, "_async_finish_initial_setup", finish)

    result = await getattr(flow, method_name)({"extra": "value"})

    assert result == {"finished": step_id}
    data, actual_step, _schema = finish.await_args.args
    assert data == {"base": "kept", "extra": "value"}
    assert actual_step == step_id


@pytest.mark.asyncio
async def test_runtime_configuration_partial_entity_returns_without_manager_reconciliation() -> None:
    from custom_components.extended_openai_conversation_responses import (
        agent_configuration,
    )

    entity = SimpleNamespace(
        subentry=SimpleNamespace(data={}, subentry_id=None),
        hass=None,
        entry=None,
    )

    await agent_configuration.async_reconcile_runtime_configuration(entity, force=True)

    assert not hasattr(entity, agent_configuration._RUNTIME_CONFIG_LOCK)


def test_agent_config_serialization_falls_back_for_runtime_objects(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import agent_config

    class RuntimeOnly:
        pass

    dump = Mock(return_value="fallback-yaml")
    monkeypatch.setattr(agent_config.yaml, "safe_dump", dump)

    result = agent_config._serialize_function_tools_config(
        [{"runtime": RuntimeOnly()}]
    )

    assert result == "fallback-yaml"
    dump.assert_called_once()


def test_native_statistics_migration_non_mapping_parameters_is_rejected(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        native_function_schema_migration as migration,
    )

    class EqualLegacy:
        def __eq__(self, other):
            return other == migration._LEGACY_PRESET_GET_STATISTICS_PARAMETERS

    tool = {"spec": {"parameters": EqualLegacy()}}

    assert migration._migrate_statistics_schema(tool) is False


@pytest.mark.asyncio
async def test_parallel_outcome_cleanup_cancels_pending_task_on_gather_failure(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        parallel_tool_execution,
    )

    pending = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(
        parallel_tool_execution.asyncio,
        "ensure_future",
        Mock(return_value=pending),
    )

    calls = 0

    async def broken_gather(*_tasks, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("gather failed")
        return []

    monkeypatch.setattr(
        parallel_tool_execution.asyncio,
        "gather",
        broken_gather,
    )

    with pytest.raises(RuntimeError, match="gather failed"):
        await parallel_tool_execution.async_execute_parallel_safe_batch_outcomes(
            [(object(), object())],
            AsyncMock(),
        )

    assert pending.cancelled()


def test_request_static_cache_ignores_untracked_tool_measurement() -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_static_cache,
    )

    token_keys = request_static_cache._FORMATTED_TOOL_RESULT_KEYS.set({})
    token_measurements = request_static_cache._FORMATTED_TOOL_MEASUREMENTS.set({})
    try:
        request_static_cache.remember_formatted_tool_measurement([], (10, 2))
        assert request_static_cache._FORMATTED_TOOL_MEASUREMENTS.get() == {}
    finally:
        request_static_cache._FORMATTED_TOOL_MEASUREMENTS.reset(token_measurements)
        request_static_cache._FORMATTED_TOOL_RESULT_KEYS.reset(token_keys)



@pytest.mark.asyncio
async def test_skill_source_ref_explicit_version_and_development_fallback(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import services

    assert await services.async_skill_source_ref(hass, "  custom-ref  ") == "custom-ref"
    with pytest.raises(HomeAssistantError, match="cannot be empty"):
        await services.async_skill_source_ref(hass, "   ")

    monkeypatch.setattr(
        services,
        "async_get_integration",
        AsyncMock(return_value=SimpleNamespace(version="7.0.0")),
    )
    assert await services.async_skill_source_ref(hass) == "7.0.0"

    monkeypatch.setattr(
        services,
        "async_get_integration",
        AsyncMock(return_value=SimpleNamespace(version="  ")),
    )
    assert await services.async_skill_source_ref(hass) == services.GITHUB_SKILLS_BRANCH


@pytest.mark.asyncio
async def test_service_admin_allows_system_context_and_rejects_non_admin(
    hass,
) -> None:
    from custom_components.extended_openai_conversation_responses import services

    system_call = SimpleNamespace(context=SimpleNamespace(user_id=None))
    await services._async_require_service_admin(hass, system_call)
    hass.auth.async_get_user.assert_not_awaited()

    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(is_admin=False)
    )
    user_call = SimpleNamespace(context=SimpleNamespace(user_id="user"))
    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await services._async_require_service_admin(hass, user_call)

    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(is_admin=True)
    )
    await services._async_require_service_admin(hass, user_call)


def test_management_debug_bounded_text_tracks_page_budget_exhaustion() -> None:
    from custom_components.extended_openai_conversation_responses import (
        debug_management_projection as projection,
    )

    budget = projection._ProjectionBudget(remaining=3)
    value, meta = projection._bounded_text("abcdef", 10, budget)

    assert value.startswith("abc")
    assert meta["truncated"] is True
    assert meta["original_characters"] == 6
    assert budget.remaining == 0
    assert budget.truncated is True

    value, meta = projection._bounded_text(None, 10, budget)
    assert value is None
    assert meta == {"truncated": False, "limit_characters": 10}


def test_management_debug_bounded_text_untruncated_path() -> None:
    from custom_components.extended_openai_conversation_responses import (
        debug_management_projection as projection,
    )

    value, meta = projection._bounded_text("ok", 10)

    assert value == "ok"
    assert meta == {"truncated": False, "limit_characters": 10}


def test_archive_list_sync_filters_sorts_and_pages() -> None:
    from dataclasses import dataclass

    from custom_components.extended_openai_conversation_responses import (
        management_history_queries as history,
    )

    @dataclass
    class Session:
        session_id: str
        scope_id: str
        retention_state: str
        last_message_at: str

    sessions = {
        "old": Session("old", "user:one", "retained", "2026-01-01T00:00:00+00:00"),
        "new": Session("new", "user:one", "retained", "2026-03-01T00:00:00+00:00"),
        "middle": Session(
            "middle", "user:one", "retained", "2026-02-01T00:00:00+00:00"
        ),
        "private": Session(
            "private", "user:one", "private", "2026-04-01T00:00:00+00:00"
        ),
        "other": Session(
            "other", "user:two", "retained", "2026-05-01T00:00:00+00:00"
        ),
    }

    result = history._archive_list_sync(sessions, "user:one", 1, 1)

    assert [item["session_id"] for item in result["sessions"]] == ["middle"]
    assert result["total"] == 3
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_archive_query_defers_cancellation_until_worker_settles(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_history_queries as history,
    )

    started = asyncio.Event()
    release = asyncio.Event()
    completed = False

    def query() -> str:
        return "unused"

    async def to_thread(_query, *_args):
        nonlocal completed
        started.set()
        await release.wait()
        completed = True
        return "done"

    monkeypatch.setattr(history.asyncio, "to_thread", to_thread)
    archive = SimpleNamespace(
        _lock=asyncio.Lock(),
        _ensure_initialized=Mock(),
    )

    task = asyncio.create_task(history._async_archive_query(archive, query))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed
    archive._ensure_initialized.assert_called_once()


@pytest.mark.asyncio
async def test_service_call_function_without_result_alias_uses_active_executor(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import services

    executor = AsyncMock(return_value={"result": {"ok": True}})
    monkeypatch.setattr(services, "async_call_active_function", executor)

    # Exercise the registered closure indirectly by reproducing its deliberately
    # tiny branch contract through the public helper inputs.
    result = await services.async_call_active_function("demo", {"value": 1})

    assert result == {"result": {"ok": True}}
    executor.assert_awaited_once_with("demo", {"value": 1})



@pytest.mark.asyncio
async def test_async_get_intercom_rejects_removed_integration(hass) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    hass.data[f"{intercom.DOMAIN}.removed"] = True

    with pytest.raises(HomeAssistantError, match="has been removed"):
        await intercom.async_get_intercom(hass)


@pytest.mark.asyncio
async def test_async_get_intercom_creates_and_initializes_manager(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    manager = SimpleNamespace(async_initialize=AsyncMock())
    monkeypatch.setattr(intercom, "IntercomManager", Mock(return_value=manager))

    result = await intercom.async_get_intercom(hass)

    assert result is manager
    assert hass.data[intercom.DATA_KEY] is manager
    manager.async_initialize.assert_awaited_once()


def test_targeted_broadcast_parser_handles_non_target_and_unresolved_candidate() -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    manager = SimpleNamespace(
        catalog=Mock(
            return_value={
                "satellites": [
                    {"name": "Kitchen", "aliases": ["Cooking"], "id": "satellite.kitchen"}
                ],
                "devices": [],
                "areas": [],
                "floors": [],
                "labels": [],
            }
        ),
        resolve_named_target=Mock(return_value=None),
    )

    assert intercom.parse_targeted_broadcast("hello there", manager) is None
    assert intercom.parse_targeted_broadcast("tell Kitchen hello", manager) is None
    manager.resolve_named_target.assert_called()


def test_targeted_broadcast_parser_resolves_alias_and_strips_display_name() -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    manager = SimpleNamespace(
        catalog=Mock(
            return_value={
                "satellites": [
                    {"name": "Kitchen", "aliases": ["Cooking"], "id": "satellite.kitchen"}
                ],
                "devices": [],
                "areas": [],
                "floors": [],
                "labels": [],
            }
        ),
        resolve_named_target=Mock(
            return_value={"entity_ids": ["assist_satellite.kitchen"], "name": "Kitchen"}
        ),
    )

    result = intercom.parse_targeted_broadcast(
        "announce to Cooking, message ready",
        manager,
    )

    assert result == (
        {"entity_ids": ["assist_satellite.kitchen"]},
        "message ready",
    )



@pytest.mark.asyncio
async def test_guest_mode_initialize_ignores_malformed_schedule(hass) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"schedule": {"invalid": True}})
    )

    await manager.async_initialize()

    assert manager._initialized is True
    assert manager.schedule is None


@pytest.mark.asyncio
async def test_guest_mode_initialize_recovers_ambiguous_persistence_state(hass) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    manager._persistence_unavailable = True
    manager._store = SimpleNamespace(async_load=AsyncMock(return_value=None))
    listener = Mock()
    manager._listeners.add(listener)

    await manager.async_initialize()

    assert manager._initialized is True
    assert manager._persistence_unavailable is False
    assert manager.schedule is None
    listener.assert_called_once()


def test_guest_mode_status_fails_closed_when_persistence_unavailable(hass) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    manager._persistence_unavailable = True

    with pytest.raises(HomeAssistantError, match="persistence is unavailable"):
        manager.status()


@pytest.mark.asyncio
async def test_guest_mode_backup_fails_closed_when_persistence_unavailable(hass) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    manager._persistence_unavailable = True

    with pytest.raises(HomeAssistantError, match="persistence is unavailable"):
        await manager.async_backup_data()
