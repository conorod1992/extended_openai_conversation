"""Low-risk conversation hot-path optimizations.

This module keeps expensive configuration validation at configuration-revision
boundaries, reuses compiled Home Assistant templates and immutable memory lexical
derivations, and opts direct OpenAI GPT-5.6+ Responses requests into explicit
stable-prefix prompt caching.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache, wraps
import hashlib
import math
import re
from types import MappingProxyType
from typing import Any

import yaml

from homeassistant.helpers import template
from homeassistant.helpers.template.helpers import resolve_area_id

from .agent_config import (
    configured_function_tools_from_data as _configured_function_tools_from_data,
    validate_function_groups as _validate_function_groups,
)
from .const import (
    CONF_FUNCTION_TOOLS,
    CONF_PROMPT,
    DEFAULT_EXPOSED_ENTITIES_CONTEXT_TEMPLATE,
    DEFAULT_PROMPT,
)
from .memory import (
    MemoryRecord,
    _normalize as _memory_normalize,
    _record_token_list as _memory_record_token_list,
    _tokens as _memory_tokens,
)
from .prompt import EffectivePrompt

_TEMPLATE_MARKERS = ("{{", "{%", "{#")
_TEMPLATE_CACHE_LIMIT = 64
_MEMORY_LEXICAL_CACHE_LIMIT = 20_000
_EXPLICIT_CACHE_MIN_CHARACTERS = 4096
_GPT_56_OR_LATER = re.compile(r"^gpt-5\.(\d+)(?:[-.]|$)", re.IGNORECASE)

_TEMPLATE_CACHE: dict[tuple[int, str], template.Template] = {}
_INSTALLED = False


@dataclass(frozen=True, slots=True)
class PromptCacheContext:
    """Stable provider-visible prefix for the current conversation turn."""

    prefix: str
    key: str


_PROMPT_CACHE_CONTEXT: ContextVar[PromptCacheContext | None] = ContextVar(
    "extended_openai_prompt_cache_context", default=None
)


def _template_requires_render(raw: str) -> bool:
    """Return whether a string contains Home Assistant/Jinja template syntax."""
    return any(marker in raw for marker in _TEMPLATE_MARKERS)


def _configured_tools_yaml(data: Mapping[str, Any]) -> str | None:
    """Return a deterministic cache key preserving legacy empty/default semantics."""
    configured = data.get(CONF_FUNCTION_TOOLS)
    if not configured:
        return None
    if isinstance(configured, str):
        return configured
    return yaml.safe_dump(
        configured,
        sort_keys=True,
        allow_unicode=True,
    )


@lru_cache(maxsize=64)
def _cached_configured_tools(raw_yaml: str | None) -> tuple[dict[str, Any], ...]:
    """Parse and validate one distinct persisted Function Tool revision once."""
    data: dict[str, Any] = {}
    if raw_yaml is not None:
        data[CONF_FUNCTION_TOOLS] = raw_yaml
    return tuple(_configured_function_tools_from_data(data))


def cached_configured_function_tools_from_data(
    data: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return isolated tools from the validated configuration-revision cache."""
    # Callers historically received fresh mutable dictionaries. Keep that contract
    # while moving the much more expensive YAML/schema validation behind the cache.
    return deepcopy(list(_cached_configured_tools(_configured_tools_yaml(data))))


def _groups_cache_key(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=True, allow_unicode=True)


@lru_cache(maxsize=128)
def _cached_function_groups(
    groups_yaml: str,
    tool_names: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    groups = yaml.safe_load(groups_yaml)
    synthetic_tools = [{"spec": {"name": name}} for name in tool_names]
    return tuple(_validate_function_groups(groups, synthetic_tools))


def cached_validate_function_groups(
    value: Any, function_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate each distinct group/tool-name configuration only once."""
    try:
        tool_names = tuple(
            str(tool["spec"]["name"])
            for tool in function_tools
            if isinstance(tool, dict)
            and isinstance(tool.get("spec"), dict)
            and isinstance(tool["spec"].get("name"), str)
        )
        groups_yaml = _groups_cache_key(value)
    except Exception:
        # Preserve the existing validation/error path for malformed unexpected data.
        return _validate_function_groups(value, function_tools)
    return deepcopy(list(_cached_function_groups(groups_yaml, tool_names)))


@lru_cache(maxsize=_MEMORY_LEXICAL_CACHE_LIMIT)
def _cached_memory_record_terms(memory_record: MemoryRecord) -> tuple[str, ...]:
    """Tokenize one immutable memory record only once per record revision."""
    return tuple(_memory_record_token_list(memory_record))


def cached_memory_record_token_list(memory_record: MemoryRecord) -> list[str]:
    """Preserve the historical fresh-list contract around cached record terms."""
    return list(_cached_memory_record_terms(memory_record))


@lru_cache(maxsize=_MEMORY_LEXICAL_CACHE_LIMIT)
def _cached_memory_tokens(value: str) -> frozenset[str]:
    """Cache deterministic token sets while keeping callers isolated from mutation."""
    return frozenset(_memory_tokens(value))


def cached_memory_tokens(value: str) -> set[str]:
    """Return a fresh set backed by cached string tokenization."""
    return set(_cached_memory_tokens(value))


@lru_cache(maxsize=_MEMORY_LEXICAL_CACHE_LIMIT)
def cached_memory_normalize(value: str) -> str:
    """Cache deterministic normalized text used repeatedly by memory ranking."""
    return _memory_normalize(value)


@lru_cache(maxsize=_MEMORY_LEXICAL_CACHE_LIMIT)
def _cached_memory_term_frequencies(
    document_terms: tuple[str, ...],
) -> Mapping[str, int]:
    """Build one immutable frequency map per distinct tokenized memory document."""
    frequencies: dict[str, int] = {}
    for term in document_terms:
        frequencies[term] = frequencies.get(term, 0) + 1
    return MappingProxyType(frequencies)


def cached_memory_bm25_score(
    query_terms: list[str],
    document_terms: list[str],
    document_frequency: Mapping[str, int],
    document_count: int,
    average_length: float,
) -> float:
    """Calculate the existing BM25 score without rebuilding term frequencies."""
    frequencies = _cached_memory_term_frequencies(tuple(document_terms))
    k1, b = 1.2, 0.75
    score = 0.0
    max_score = 0.0
    for term in dict.fromkeys(query_terms):
        df = document_frequency.get(term, 0)
        idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
        tf = frequencies.get(term, 0)
        denominator = tf + k1 * (1 - b + b * len(document_terms) / average_length)
        if tf:
            score += idf * (tf * (k1 + 1) / denominator)
        max_score += idf * (k1 + 1) / (1 + k1 * (1 - b))
    return score / max_score if max_score else 0.0


def _render_default_exposed_entities(
    hass: Any,
    exposed_entities: list[dict[str, Any]],
) -> str:
    """Render the built-in entity CSV directly instead of through a Jinja loop."""
    lines = [
        "## Available Devices",
        "```csv",
        "entity_id,name,state,area_id,aliases",
    ]
    for entity in exposed_entities:
        entity_id = str(entity.get("entity_id", ""))
        aliases = entity.get("aliases") or []
        lines.append(
            f"{entity_id},{entity.get('name', '')},{entity.get('state', '')},"
            f"{resolve_area_id(hass, entity_id)},{'/'.join(str(item) for item in aliases)}"
        )
    lines.append("```")
    return "\n".join(lines) + "\n"


def optimized_render_template(
    hass: Any,
    raw: str,
    *,
    exposed_entities: list[dict[str, Any]],
    current_device_id: str | None,
    user_input: Any,
    skills: list[Any],
) -> str:
    """Render a prompt template without reparsing stable/static templates."""
    if not _template_requires_render(raw):
        return raw
    if raw == DEFAULT_EXPOSED_ENTITIES_CONTEXT_TEMPLATE:
        return _render_default_exposed_entities(hass, exposed_entities)

    key = (id(hass), raw)
    rendered_template = _TEMPLATE_CACHE.get(key)
    if rendered_template is None:
        if len(_TEMPLATE_CACHE) >= _TEMPLATE_CACHE_LIMIT:
            _TEMPLATE_CACHE.pop(next(iter(_TEMPLATE_CACHE)))
        rendered_template = template.Template(raw, hass)
        _TEMPLATE_CACHE[key] = rendered_template
    return str(
        rendered_template.async_render(
            {
                "ha_name": hass.config.location_name,
                "exposed_entities": exposed_entities,
                "current_device_id": current_device_id,
                "user_input": user_input,
                "skills": skills,
            },
            parse_result=False,
        )
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
    if len(
        prefix
    ) < _EXPLICIT_CACHE_MIN_CHARACTERS or not effective_prompt.text.startswith(prefix):
        return None
    digest = hashlib.sha256(prefix.encode()).hexdigest()[:48]
    return PromptCacheContext(prefix=prefix, key=f"eoc-{digest}")


def _supports_explicit_cache(model: Any) -> bool:
    if not isinstance(model, str):
        return False
    match = _GPT_56_OR_LATER.match(model)
    return bool(match and int(match.group(1)) >= 6)


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
    if (
        not direct_openai
        or context is None
        or not _supports_explicit_cache(kwargs.get("model"))
    ):
        return kwargs

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


def install_performance_optimizations() -> None:
    """Install hot-path hooks once; configuration/runtime semantics stay unchanged."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import agent_config, conversation, memory, prompt

    # Runtime callers repeatedly reached the authoritative validators. Retain those
    # validators at save/load boundaries while reusing the result for an unchanged
    # persisted configuration revision.
    agent_config.configured_function_tools_from_data = (  # type: ignore[assignment]
        cached_configured_function_tools_from_data
    )
    conversation.configured_function_tools_from_data = (  # type: ignore[assignment]
        cached_configured_function_tools_from_data
    )
    agent_config.validate_function_groups = cached_validate_function_groups  # type: ignore[assignment]
    conversation.validate_function_groups = cached_validate_function_groups  # type: ignore[assignment]

    # Persistent Memory records are frozen and record updates replace the object.
    # Cache only deterministic lexical derivations underneath the existing ranking
    # algorithm; query parsing, candidate selection and ranking stay authoritative.
    memory._record_token_list = cached_memory_record_token_list  # type: ignore[assignment]
    memory._tokens = cached_memory_tokens  # type: ignore[attr-defined]
    memory._normalize = cached_memory_normalize  # type: ignore[attr-defined]
    memory._bm25_score = cached_memory_bm25_score  # type: ignore[attr-defined]

    # render_effective_prompt resolves this module global at call time, so replacing
    # the helper speeds both live prompts and previews without changing their output.
    prompt._render_template = optimized_render_template  # type: ignore[attr-defined]

    original_render_effective_prompt = conversation.render_effective_prompt

    @wraps(original_render_effective_prompt)
    def render_effective_prompt_with_cache_context(*args: Any, **kwargs: Any) -> Any:
        effective = original_render_effective_prompt(*args, **kwargs)
        options = args[1] if len(args) > 1 else kwargs.get("options", {})
        _PROMPT_CACHE_CONTEXT.set(prompt_cache_context(effective, options))
        return effective

    conversation.render_effective_prompt = render_effective_prompt_with_cache_context

    original_process = conversation.ExtendedOpenAIAgentEntity._async_process

    @wraps(original_process)
    async def process_with_fresh_cache_context(self: Any, user_input: Any) -> Any:
        token = _PROMPT_CACHE_CONTEXT.set(None)
        try:
            return await original_process(self, user_input)
        finally:
            _PROMPT_CACHE_CONTEXT.reset(token)

    conversation.ExtendedOpenAIAgentEntity._async_process = (  # type: ignore[method-assign]
        process_with_fresh_cache_context
    )


def performance_cache_info() -> dict[str, Any]:
    """Return non-sensitive cache counters useful to tests/diagnostics."""
    tool_info = _cached_configured_tools.cache_info()
    group_info = _cached_function_groups.cache_info()
    return {
        "configured_tool_cache_hits": tool_info.hits,
        "configured_tool_cache_misses": tool_info.misses,
        "function_group_cache_hits": group_info.hits,
        "function_group_cache_misses": group_info.misses,
        "compiled_template_count": len(_TEMPLATE_CACHE),
    }
