"""Tests for conversation hot-path optimizations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    memory,
    prompt,
    prompt_cache as performance,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    _cached_configured_tools,
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.const import CONF_PROMPT
from custom_components.extended_openai_conversation_responses.prompt import (
    EffectivePrompt,
    PromptSection,
    _render_template,
)
from custom_components.extended_openai_conversation_responses.prompt_cache import (
    optimize_responses_kwargs,
    prompt_cache_context,
)


def _effective_prompt(raw_prompt: str, *, dynamic: bool = False) -> EffectivePrompt:
    user = PromptSection(
        "user_prompt",
        "Rendered user prompt",
        raw_prompt,
        "mixed",
    )
    if not dynamic:
        return EffectivePrompt(raw_prompt, (user,))
    volatile = PromptSection(
        "current_datetime_context",
        "Current date/time context",
        "dynamic value",
        "volatile",
    )
    return EffectivePrompt(f"{raw_prompt}\ndynamic value", (user, volatile))


def test_static_template_fast_path_preserves_text() -> None:
    raw = "Static assistant instructions with no template syntax."
    assert (
        _render_template(
            None,
            raw,
            exposed_entities=[],
            current_device_id=None,
            user_input=None,
            skills=[],
        )
        == raw
    )


def test_configured_tool_validation_is_cached_by_revision(hass) -> None:
    _cached_configured_tools.cache_clear()

    first = configured_function_tools_from_data({})
    second = configured_function_tools_from_data({})

    assert first == second
    assert first is not second
    info = _cached_configured_tools.cache_info()
    assert info.misses == 1
    assert info.hits == 1


def test_explicit_cache_marks_only_stable_system_prefix() -> None:
    raw_prompt = "A" * 5000
    effective = _effective_prompt(raw_prompt, dynamic=True)
    context = prompt_cache_context(effective, {CONF_PROMPT: raw_prompt})

    assert context is not None
    assert context.prefix == f"{raw_prompt}\n"

    kwargs = {
        "model": "gpt-5.6-luna",
        "input": [
            {
                "type": "message",
                "role": "system",
                "content": effective.text,
            },
            {"type": "message", "role": "user", "content": "hello"},
        ],
    }
    optimized = optimize_responses_kwargs(
        kwargs,
        direct_openai=True,
        cache_context=context,
    )

    assert optimized is not kwargs
    assert optimized["prompt_cache_key"] == context.key
    assert optimized["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
    content = optimized["input"][0]["content"]
    assert content == [
        {
            "type": "input_text",
            "text": f"{raw_prompt}\n",
            "prompt_cache_breakpoint": {"mode": "explicit"},
        },
        {"type": "input_text", "text": "dynamic value"},
    ]
    # The caller's request remains untouched for later tool rounds/history assembly.
    assert kwargs["input"][0]["content"] == effective.text


def test_dynamic_user_prompt_is_not_misclassified_as_stable() -> None:
    raw_prompt = "{{ now() }}" + ("A" * 5000)
    effective = _effective_prompt(raw_prompt)
    assert prompt_cache_context(effective, {CONF_PROMPT: raw_prompt}) is None


def test_cache_optimization_is_direct_openai_and_explicit_cache_is_gpt56_only() -> None:
    raw_prompt = "A" * 5000
    effective = _effective_prompt(raw_prompt)
    context = prompt_cache_context(effective, {CONF_PROMPT: raw_prompt})
    assert context is not None

    base = {
        "input": [
            {"type": "message", "role": "system", "content": effective.text},
        ]
    }
    custom_provider = {**base, "model": "gpt-5.6-luna"}
    assert (
        optimize_responses_kwargs(
            custom_provider,
            direct_openai=False,
            cache_context=context,
        )
        is custom_provider
    )

    older_model = {**base, "model": "gpt-5.5"}
    optimized = optimize_responses_kwargs(
        older_model,
        direct_openai=True,
        cache_context=context,
    )
    assert optimized is not older_model
    assert optimized["prompt_cache_key"] == context.key
    assert "prompt_cache_options" not in optimized
    assert optimized["input"] == older_model["input"]


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

    def _validate(
        value: Any, function_tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        calls.append((value, function_tools))
        return [{"validated": True}]

    monkeypatch.setattr(agent_config, "_validate_function_groups", _validate)
    value = object()

    assert agent_config.validate_function_groups(value, tools) == [{"validated": True}]
    assert calls == [(value, tools)]


def test_cached_bm25_scores_matching_terms_and_zero_match() -> None:
    """The cached scorer preserves useful BM25 matching and empty-match semantics."""
    memory._cached_memory_term_frequencies.cache_clear()

    score = memory._bm25_score(
        ["kitchen", "kitchen", "light"],
        ["kitchen", "light", "kitchen"],
        {"kitchen": 2, "light": 1},
        document_count=4,
        average_length=3.0,
    )
    assert 0 < score <= 1

    assert (
        memory._bm25_score(
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

    monkeypatch.setattr(prompt, "resolve_area_id", _resolve)
    rendered = prompt._render_default_exposed_entities(
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
    monkeypatch.setattr(prompt.template, "Template", _Template)
    monkeypatch.setattr(prompt, "_TEMPLATE_CACHE_LIMIT", 1)
    prompt._TEMPLATE_CACHE.clear()
    prompt._TEMPLATE_CACHE[(123, "old")] = object()  # type: ignore[assignment]

    kwargs = {
        "exposed_entities": [],
        "current_device_id": None,
        "user_input": "hello",
        "skills": [],
    }
    raw = "{{ user_input }}"
    first = prompt._render_template(hass, raw, **kwargs)
    second = prompt._render_template(hass, raw, **kwargs)

    assert first == second == "{{ user_input }}:Home:hello"
    assert created == [(raw, hass)]
    assert list(prompt._TEMPLATE_CACHE) == [(id(hass), raw)]
    prompt._TEMPLATE_CACHE.clear()


def test_prompt_cache_context_rejects_unusable_prefixes() -> None:
    """Caching requires a stable prefix matching the effective prompt."""
    assert performance.prompt_cache_context(EffectivePrompt("", ()), {}) is None

    volatile = EffectivePrompt(
        "dynamic",
        (_section("current_datetime_context", "dynamic", volatility="volatile"),),
    )
    assert performance.prompt_cache_context(volatile, {}) is None

    short = EffectivePrompt("short", (_section("system", "short"),))
    short_context = performance.prompt_cache_context(short, {})
    assert short_context is not None
    assert short_context.prefix == "short"

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
            "input": [{"type": "message", "role": "system", "content": "prefix"}],
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
    assert calls == [(("arg",), {"value": 1, "optimized": True})]
    assert proxy.responses.marker == "responses-marker"
    assert proxy.chat is delegate.chat
    assert proxy.embeddings is delegate.embeddings
    assert proxy.extra == "delegate-extra"


@pytest.mark.parametrize(
    "failure", [RuntimeError, __import__("asyncio").CancelledError]
)
async def test_turn_cache_context_restored_on_early_failure(failure) -> None:
    """Even failures before guest/continuity setup restore the caller's context."""
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    observed = []

    class Input:
        def as_llm_context(self, _domain):
            observed.append(performance._PROMPT_CACHE_CONTEXT.get())
            raise failure()

    outer = performance.PromptCacheContext("outer", "outer-key")
    token = performance._PROMPT_CACHE_CONTEXT.set(outer)
    try:
        with pytest.raises(failure):
            await ExtendedOpenAIAgentEntity._async_process_with_continuity(
                SimpleNamespace(), Input()
            )
        assert observed == [None]
        assert performance._PROMPT_CACHE_CONTEXT.get() is outer
    finally:
        performance._PROMPT_CACHE_CONTEXT.reset(token)


def test_system_prompt_sets_context_from_final_render(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import conversation

    text = "A" * 5000
    effective = EffectivePrompt(text, (_section("system", text),))
    monkeypatch.setattr(
        conversation, "render_effective_prompt", lambda *args, **kwargs: effective
    )
    agent = SimpleNamespace(
        hass=object(),
        subentry=SimpleNamespace(data={}),
        _effective_guest_policy=lambda: SimpleNamespace(guest_active=False),
        _get_enabled_skills=lambda: [],
        _knowledge_available=False,
    )
    token = performance._PROMPT_CACHE_CONTEXT.set(None)
    try:
        assert (
            conversation.ExtendedOpenAIAgentEntity._build_system_prompt(
                agent, [], SimpleNamespace(device_id=None), None
            )
            == text
        )
        assert performance._PROMPT_CACHE_CONTEXT.get().prefix == text
    finally:
        performance._PROMPT_CACHE_CONTEXT.reset(token)
