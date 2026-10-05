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
