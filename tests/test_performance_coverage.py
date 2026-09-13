"""Focused coverage for performance optimization fallbacks and hot paths."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import performance
from custom_components.extended_openai_conversation_responses.const import CONF_PROMPT
from custom_components.extended_openai_conversation_responses.prompt import (
    EffectivePrompt,
    PromptSection,
)


def _section(
    key: str,
    text: str,
    *,
    volatility: str = "stable",
) -> PromptSection:
    return PromptSection(key, text, text, volatility)


def test_function_group_cache_falls_back_when_key_serialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected unserializable values retain the authoritative validator path."""
    calls: list[tuple[Any, list[dict[str, Any]]]] = []
    tools = [{"spec": {"name": "tool_a"}}]

    def _validate(value: Any, function_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        calls.append((value, function_tools))
        return [{"validated": True}]

    monkeypatch.setattr(performance, "_validate_function_groups", _validate)
    value = object()

    assert performance.cached_validate_function_groups(value, tools) == [
        {"validated": True}
    ]
    assert calls == [(value, tools)]


def test_cached_bm25_scores_matching_terms_and_zero_match() -> None:
    """The cached scorer preserves useful BM25 matching and empty-match semantics."""
    performance._cached_memory_term_frequencies.cache_clear()

    score = performance.cached_memory_bm25_score(
        ["kitchen", "kitchen", "light"],
        ["kitchen", "light", "kitchen"],
        {"kitchen": 2, "light": 1},
        document_count=4,
        average_length=3.0,
    )
    assert 0 < score <= 1

    assert (
        performance.cached_memory_bm25_score(
            [],
            ["kitchen"],
            {"kitchen": 1},
            document_count=1,
            average_length=1.0,
        )
        == 0.0
    )


def test_default_exposed_entities_renderer_handles_aliases_and_missing_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct CSV fast path preserves entity rows without Jinja rendering."""
    resolved: list[str] = []

    def _resolve(_hass: Any, entity_id: str) -> str:
        resolved.append(entity_id)
        return "Kitchen"

    monkeypatch.setattr(performance, "resolve_area_id", _resolve)
    rendered = performance._render_default_exposed_entities(
        object(),
        [
            {
                "entity_id": "light.kitchen",
                "name": "Kitchen Light",
                "state": "on",
                "aliases": ["Main", 2],
            },
            {"entity_id": "sensor.empty", "aliases": None},
        ],
    )

    assert rendered == (
        "## Available Devices\n"
        "```csv\n"
        "entity_id,name,state,area_id,aliases\n"
        "light.kitchen,Kitchen Light,on,Kitchen,Main/2\n"
        "sensor.empty,,,Kitchen,\n"
        "```\n"
    )
    assert resolved == ["light.kitchen", "sensor.empty"]


def test_template_cache_evicts_oldest_entry_and_reuses_compiled_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dynamic templates are compiled once and the bounded cache evicts oldest data."""
    created: list[tuple[str, Any]] = []

    class _Template:
        def __init__(self, raw: str, hass: Any) -> None:
            created.append((raw, hass))
            self.raw = raw

        def async_render(self, variables: dict[str, Any], *, parse_result: bool) -> str:
            assert parse_result is False
            return f"{self.raw}:{variables['ha_name']}:{variables['user_input']}"

    hass = SimpleNamespace(config=SimpleNamespace(location_name="Home"))
    monkeypatch.setattr(performance.template, "Template", _Template)
    monkeypatch.setattr(performance, "_TEMPLATE_CACHE_LIMIT", 1)
    performance._TEMPLATE_CACHE.clear()
    performance._TEMPLATE_CACHE[(123, "old")] = object()  # type: ignore[assignment]

    kwargs = {
        "exposed_entities": [],
        "current_device_id": None,
        "user_input": "hello",
        "skills": [],
    }
    raw = "{{ user_input }}"
    first = performance.optimized_render_template(hass, raw, **kwargs)
    second = performance.optimized_render_template(hass, raw, **kwargs)

    assert first == second == "{{ user_input }}:Home:hello"
    assert created == [(raw, hass)]
    assert list(performance._TEMPLATE_CACHE) == [(id(hass), raw)]
    performance._TEMPLATE_CACHE.clear()


def test_prompt_cache_context_rejects_unusable_prefixes() -> None:
    """Explicit caching requires a stable, long prefix matching the effective prompt."""
    assert performance.prompt_cache_context(EffectivePrompt("", ()), {}) is None

    volatile = EffectivePrompt(
        "dynamic",
        (_section("current_datetime_context", "dynamic", volatility="volatile"),),
    )
    assert performance.prompt_cache_context(volatile, {}) is None

    short = EffectivePrompt("short", (_section("system", "short"),))
    assert performance.prompt_cache_context(short, {}) is None

    long_text = "A" * 5000
    mismatched = EffectivePrompt(
        "different text",
        (_section("system", long_text),),
    )
    assert performance.prompt_cache_context(mismatched, {}) is None


def test_prompt_cache_context_assembles_multiple_stable_sections() -> None:
    """All deterministic leading sections contribute to the exact cache prefix."""
    first = "A" * 2500
    second = "B" * 2500
    text = f"{first}\n{second}"
    effective = EffectivePrompt(
        text,
        (
            _section("system", first),
            _section("user_prompt", second, volatility="mixed"),
        ),
    )

    context = performance.prompt_cache_context(effective, {CONF_PROMPT: second})

    assert context is not None
    assert context.prefix == text
    assert context.key.startswith("eoc-")


def test_explicit_cache_support_and_request_shape_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported models and malformed Responses inputs remain completely untouched."""
    context = performance.PromptCacheContext(prefix="prefix", key="cache-key")
    monkeypatch.setattr(
        performance,
        "model_metadata",
        lambda model: {"explicit_prompt_cache": model == "supported"},
    )

    assert performance._supports_explicit_cache(None) is False
    assert performance._supports_explicit_cache("supported") is True

    cases = [
        {"model": "supported"},
        {"model": "supported", "input": []},
        {"model": "supported", "input": ["system"]},
        {"model": "supported", "input": [{"type": "other"}]},
        {
            "model": "supported",
            "input": [{"type": "message", "role": "user", "content": "prefix"}],
        },
        {
            "model": "supported",
            "input": [{"type": "message", "role": "system", "content": ["prefix"]}],
        },
        {
            "model": "supported",
            "input": [{"type": "message", "role": "system", "content": "different"}],
        },
    ]
    for kwargs in cases:
        assert (
            performance.optimize_responses_kwargs(
                kwargs,
                direct_openai=True,
                cache_context=context,
            )
            is kwargs
        )


def test_explicit_cache_uses_contextvar_and_preserves_existing_cache_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Context-local cache metadata is used without overriding caller-supplied options."""
    context = performance.PromptCacheContext(prefix="prefix", key="generated-key")
    monkeypatch.setattr(
        performance,
        "model_metadata",
        lambda _model: {"explicit_prompt_cache": True},
    )
    token = performance._PROMPT_CACHE_CONTEXT.set(context)
    try:
        kwargs = {
            "model": "supported",
            "input": [
                {"type": "message", "role": "system", "content": "prefix"}
            ],
            "prompt_cache_key": "caller-key",
            "prompt_cache_options": {"mode": "caller"},
        }
        optimized = performance.optimize_responses_kwargs(
            kwargs,
            direct_openai=True,
        )
    finally:
        performance._PROMPT_CACHE_CONTEXT.reset(token)

    assert optimized["prompt_cache_key"] == "caller-key"
    assert optimized["prompt_cache_options"] == {"mode": "caller"}
    assert optimized["input"][0]["content"] == [
        {
            "type": "input_text",
            "text": "prefix",
            "prompt_cache_breakpoint": {"mode": "explicit"},
        }
    ]
    assert kwargs["input"][0]["content"] == "prefix"


async def test_openai_client_proxy_delegates_responses_and_other_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy optimizes Responses calls while remaining transparent elsewhere."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class _Responses:
        marker = "responses-marker"

        async def create(self, *args: Any, **kwargs: Any) -> str:
            calls.append((args, kwargs))
            return "created"

    delegate = SimpleNamespace(
        responses=_Responses(),
        chat=object(),
        embeddings=object(),
        extra="delegate-extra",
    )
    monkeypatch.setattr(
        performance,
        "optimize_responses_kwargs",
        lambda kwargs, *, direct_openai: {**kwargs, "optimized": direct_openai},
    )

    proxy = performance.PerformanceOpenAIClientProxy(delegate, direct_openai=True)

    assert await proxy.responses.create("arg", value=1) == "created"
    assert calls == [(('arg',), {"value": 1, "optimized": True})]
    assert proxy.responses.marker == "responses-marker"
    assert proxy.chat is delegate.chat
    assert proxy.embeddings is delegate.embeddings
    assert proxy.extra == "delegate-extra"


async def test_install_hooks_capture_options_and_reset_turn_cache_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed wrappers populate prompt cache metadata and isolate it per turn."""
    from custom_components.extended_openai_conversation_responses import (
        agent_config,
        conversation,
        memory,
        prompt,
    )

    attrs = {
        (agent_config, "configured_function_tools_from_data"): agent_config.configured_function_tools_from_data,
        (agent_config, "validate_function_groups"): agent_config.validate_function_groups,
        (conversation, "configured_function_tools_from_data"): conversation.configured_function_tools_from_data,
        (conversation, "validate_function_groups"): conversation.validate_function_groups,
        (conversation, "render_effective_prompt"): conversation.render_effective_prompt,
        (conversation.ExtendedOpenAIAgentEntity, "_async_process"): conversation.ExtendedOpenAIAgentEntity._async_process,
        (memory, "_record_token_list"): memory._record_token_list,
        (memory, "_tokens"): memory._tokens,
        (memory, "_normalize"): memory._normalize,
        (memory, "_bm25_score"): memory._bm25_score,
        (prompt, "_render_template"): prompt._render_template,
    }
    previous_installed = performance._INSTALLED
    previous_context = performance._PROMPT_CACHE_CONTEXT.get()
    observed_options: list[Any] = []
    observed_process_contexts: list[Any] = []
    effective = EffectivePrompt("effective", (_section("system", "effective"),))

    def _render(*_args: Any, **_kwargs: Any) -> EffectivePrompt:
        return effective

    async def _process(_self: Any, _user_input: Any) -> None:
        observed_process_contexts.append(performance._PROMPT_CACHE_CONTEXT.get())
        raise RuntimeError("process failed")

    conversation.render_effective_prompt = _render
    conversation.ExtendedOpenAIAgentEntity._async_process = _process
    monkeypatch.setattr(
        performance,
        "prompt_cache_context",
        lambda _effective, options: (
            observed_options.append(options)
            or performance.PromptCacheContext(prefix="prefix", key="key")
        ),
    )
    performance._INSTALLED = False

    try:
        performance.install_performance_optimizations()
        performance.install_performance_optimizations()

        positional_options = {"source": "positional"}
        assert conversation.render_effective_prompt("hass", positional_options) is effective
        assert performance._PROMPT_CACHE_CONTEXT.get() == performance.PromptCacheContext(
            prefix="prefix", key="key"
        )

        keyword_options = {"source": "keyword"}
        assert conversation.render_effective_prompt("hass", options=keyword_options) is effective
        assert observed_options == [positional_options, keyword_options]

        outer = performance.PromptCacheContext(prefix="outer", key="outer-key")
        token = performance._PROMPT_CACHE_CONTEXT.set(outer)
        try:
            with pytest.raises(RuntimeError, match="process failed"):
                await conversation.ExtendedOpenAIAgentEntity._async_process(
                    SimpleNamespace(), "hello"
                )
            assert observed_process_contexts == [None]
            assert performance._PROMPT_CACHE_CONTEXT.get() == outer
        finally:
            performance._PROMPT_CACHE_CONTEXT.reset(token)
    finally:
        for (target, name), value in attrs.items():
            setattr(target, name, value)
        performance._INSTALLED = previous_installed
        performance._PROMPT_CACHE_CONTEXT.set(previous_context)


def test_performance_cache_info_reports_all_public_counters() -> None:
    """Diagnostics expose cache hits/misses and compiled-template size."""
    performance._cached_configured_tools.cache_clear()
    performance._cached_function_groups.cache_clear()
    performance._TEMPLATE_CACHE.clear()

    performance._cached_configured_tools(None)
    performance._cached_configured_tools(None)
    performance._TEMPLATE_CACHE[(1, "template")] = object()  # type: ignore[assignment]

    info = performance.performance_cache_info()

    assert info["configured_tool_cache_hits"] == 1
    assert info["configured_tool_cache_misses"] == 1
    assert info["function_group_cache_hits"] == 0
    assert info["function_group_cache_misses"] == 0
    assert info["compiled_template_count"] == 1
    performance._TEMPLATE_CACHE.clear()
