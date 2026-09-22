"""Explicit Responses prompt-cache request integration."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from typing import Any

from .const import CONF_PROMPT, DEFAULT_PROMPT
from .model_catalog import model_metadata
from .prompt import EffectivePrompt, _template_requires_render


@dataclass(frozen=True, slots=True)
class PromptCacheContext:
    """Stable provider-visible prefix for the current conversation turn."""

    prefix: str
    key: str


_PROMPT_CACHE_CONTEXT: ContextVar[PromptCacheContext | None] = ContextVar(
    "extended_openai_prompt_cache_context", default=None
)


def _assemble_sections(sections: tuple[Any, ...]) -> str:
    assembled = str(sections[0].text)
    for section in sections[1:]:
        assembled = f"{assembled.rstrip()}\n{section.text}"
    return assembled


def prompt_cache_context(
    effective_prompt: EffectivePrompt,
    options: Mapping[str, Any],
) -> PromptCacheContext | None:
    """Return the longest deterministic leading prompt section for GPT-5.6 cache use."""
    sections = effective_prompt.sections
    if not sections:
        return None

    raw_user_prompt = str(options.get(CONF_PROMPT, DEFAULT_PROMPT))
    stable_count = 0
    for section in sections:
        stable = section.volatility == "stable"
        if section.key == "user_prompt" and not _template_requires_render(
            raw_user_prompt
        ):
            stable = True
        if not stable:
            break
        stable_count += 1

    if stable_count == 0:
        return None
    prefix = _assemble_sections(sections[:stable_count])
    if stable_count < len(sections):
        # Match the exact separator used when the next, volatile section is appended.
        prefix = prefix.rstrip() + "\n"
    if not effective_prompt.text.startswith(prefix):
        return None
    digest = hashlib.sha256(prefix.encode()).hexdigest()[:48]
    return PromptCacheContext(prefix=prefix, key=f"eoc-{digest}")


def _supports_explicit_cache(model: Any) -> bool:
    if not isinstance(model, str):
        return False
    return bool(model_metadata(model)["explicit_prompt_cache"])


def optimize_responses_kwargs(
    kwargs: dict[str, Any],
    *,
    direct_openai: bool,
    cache_context: PromptCacheContext | None = None,
) -> dict[str, Any]:
    """Mark only the stable system-prompt prefix as cacheable for GPT-5.6+."""
    context = (
        cache_context if cache_context is not None else _PROMPT_CACHE_CONTEXT.get()
    )
    if not direct_openai or context is None:
        return kwargs

    if not _supports_explicit_cache(kwargs.get("model")):
        optimized = dict(kwargs)
        optimized.setdefault("prompt_cache_key", context.key)
        return optimized

    input_items = kwargs.get("input")
    if not isinstance(input_items, list) or not input_items:
        return kwargs
    first = input_items[0]
    if (
        not isinstance(first, dict)
        or first.get("type") != "message"
        or first.get("role") != "system"
        or not isinstance(first.get("content"), str)
    ):
        return kwargs
    content = first["content"]
    if not content.startswith(context.prefix):
        return kwargs

    suffix = content[len(context.prefix) :]
    content_blocks: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": context.prefix,
            "prompt_cache_breakpoint": {"mode": "explicit"},
        }
    ]
    if suffix:
        content_blocks.append({"type": "input_text", "text": suffix})

    optimized = dict(kwargs)
    optimized_input = list(input_items)
    optimized_first = dict(first)
    optimized_first["content"] = content_blocks
    optimized_input[0] = optimized_first
    optimized["input"] = optimized_input
    optimized.setdefault("prompt_cache_key", context.key)
    optimized.setdefault(
        "prompt_cache_options",
        {"mode": "explicit", "ttl": "30m"},
    )
    return optimized


class _PerformanceResponsesProxy:
    def __init__(self, delegate: Any, direct_openai: bool) -> None:
        self._delegate = delegate
        self._direct_openai = direct_openai

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        return await self._delegate.create(
            *args,
            **optimize_responses_kwargs(kwargs, direct_openai=self._direct_openai),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class PerformanceOpenAIClientProxy:
    """Transparent client proxy for direct-OpenAI request optimizations."""

    def __init__(self, delegate: Any, *, direct_openai: bool) -> None:
        self._delegate = delegate
        self.responses = _PerformanceResponsesProxy(delegate.responses, direct_openai)
        self.chat = delegate.chat
        self.embeddings = delegate.embeddings

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)
