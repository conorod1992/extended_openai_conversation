"""Tests for conversation hot-path optimizations."""

from custom_components.extended_openai_conversation_responses.const import CONF_PROMPT
from custom_components.extended_openai_conversation_responses.performance import (
    _cached_configured_tools,
    cached_configured_function_tools_from_data,
    optimize_responses_kwargs,
    optimized_render_template,
    prompt_cache_context,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    EffectivePrompt,
    PromptSection,
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
        optimized_render_template(
            None,
            raw,
            exposed_entities=[],
            current_device_id=None,
            user_input=None,
            skills=[],
        )
        == raw
    )


def test_configured_tool_validation_is_cached_by_revision() -> None:
    _cached_configured_tools.cache_clear()

    first = cached_configured_function_tools_from_data({})
    second = cached_configured_function_tools_from_data({})

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


def test_explicit_cache_is_direct_openai_gpt56_only() -> None:
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
    assert (
        optimize_responses_kwargs(
            older_model,
            direct_openai=True,
            cache_context=context,
        )
        is older_model
    )
