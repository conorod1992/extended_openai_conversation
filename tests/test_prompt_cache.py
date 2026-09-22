"""Tests for provider prompt-cache request optimization."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses.prompt import (
    EffectivePrompt,
    PromptSection,
)
from custom_components.extended_openai_conversation_responses.prompt_cache import (
    PromptCacheContext,
    optimize_responses_kwargs,
    prompt_cache_context,
)


def test_short_stable_prefix_is_exposed_without_character_heuristic() -> None:
    """Provider token eligibility, not a local character estimate, decides caching."""
    prompt = EffectivePrompt(
        text="stable guidance\nvolatile context",
        sections=(
            PromptSection("guidance", "Guidance", "stable guidance", "stable"),
            PromptSection("context", "Context", "volatile context", "volatile"),
        ),
    )

    context = prompt_cache_context(prompt, {})

    assert context is not None
    assert context.prefix == "stable guidance\n"
    assert context.key.startswith("eoc-")


def test_pre_56_direct_openai_request_gets_only_stable_cache_key() -> None:
    """Older models get cache routing without 5.6-only cache controls."""
    context = PromptCacheContext(prefix="stable\n", key="eoc-stable")
    kwargs = {
        "model": "gpt-5.5",
        "input": [
            {
                "type": "message",
                "role": "system",
                "content": "stable\nvolatile",
            }
        ],
    }

    optimized = optimize_responses_kwargs(
        kwargs,
        direct_openai=True,
        cache_context=context,
    )

    assert optimized["prompt_cache_key"] == "eoc-stable"
    assert "prompt_cache_options" not in optimized
    assert optimized["input"] == kwargs["input"]
    assert kwargs.get("prompt_cache_key") is None


def test_pre_56_cache_key_does_not_override_explicit_caller_value() -> None:
    context = PromptCacheContext(prefix="stable\n", key="eoc-stable")
    kwargs = {
        "model": "gpt-5.4",
        "prompt_cache_key": "caller-key",
        "input": [],
    }

    optimized = optimize_responses_kwargs(
        kwargs,
        direct_openai=True,
        cache_context=context,
    )

    assert optimized["prompt_cache_key"] == "caller-key"


def test_gpt_56_keeps_explicit_only_breakpoint_policy() -> None:
    """The cache-routing cleanup must not switch 5.6 to implicit mode."""
    context = PromptCacheContext(prefix="stable\n", key="eoc-stable")
    kwargs = {
        "model": "gpt-5.6",
        "input": [
            {
                "type": "message",
                "role": "system",
                "content": "stable\nvolatile",
            }
        ],
    }

    optimized = optimize_responses_kwargs(
        kwargs,
        direct_openai=True,
        cache_context=context,
    )

    assert optimized["prompt_cache_key"] == "eoc-stable"
    assert optimized["prompt_cache_options"] == {
        "mode": "explicit",
        "ttl": "30m",
    }
    assert optimized["input"][0]["content"] == [
        {
            "type": "input_text",
            "text": "stable\n",
            "prompt_cache_breakpoint": {"mode": "explicit"},
        },
        {"type": "input_text", "text": "volatile"},
    ]


def test_non_openai_endpoint_is_unchanged() -> None:
    context = PromptCacheContext(prefix="stable\n", key="eoc-stable")
    kwargs = {"model": "gpt-5.5", "input": []}

    optimized = optimize_responses_kwargs(
        kwargs,
        direct_openai=False,
        cache_context=context,
    )

    assert optimized is kwargs
    assert "prompt_cache_key" not in optimized
