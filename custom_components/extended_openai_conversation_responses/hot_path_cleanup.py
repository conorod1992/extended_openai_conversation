"""Very low-risk conversation hot-path cleanup.

These optimizations remove bookkeeping or redundant matching work that cannot change
provider input, tool availability, or the selected deterministic Request Rule.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, cast

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_USAGE_PRUNE_TASK = "_extended_openai_usage_prune_task"


def install_hot_path_cleanup() -> None:
    """Install post-lifecycle hot-path optimizations once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_usage_lazy_snapshots_and_background_prune()
    _install_debug_single_conversion()
    _install_broadcast_cold_path_guard()
    _install_request_rule_two_pass_matching()
    _INSTALLED = True


def _install_usage_lazy_snapshots_and_background_prune() -> None:
    """Defer usage serialization and daily retention work beyond the user turn."""
    from . import lifecycle_optimizations as lifecycle
    from .usage import UsageManager

    manager_type: Any = UsageManager
    previous_save_safely = manager_type._async_save_safely
    previous_prune_if_due = lifecycle._async_prune_usage_if_due
    transactional_prune = bool(
        getattr(
            manager_type.async_prune_details,
            "_extended_openai_persist_first",
            False,
        )
    )

    async def async_save_safely(manager: Any, label: str, save: Any) -> None:
        category = {
            "request totals": "totals",
            "run totals": "totals",
            "daily run totals": "daily",
            "request details": "details",
            "run details": "details",
        }.get(label)
        store = None
        if category is not None:
            store = {
                "totals": manager._storage,
                "daily": manager._daily_storage,
                "details": manager._detail_storage,
            }.get(category)
        delay_save = getattr(store, "async_delay_save", None)
        if category is not None and callable(delay_save):
            try:
                # Home Assistant evaluates this callback when the coalesced save is
                # actually due. Manager mutations are event-loop serialized, and the
                # snapshot helper does not await, so it observes one coherent latest
                # in-memory state without making the provider/tool loop serialize the
                # retained history first.
                delay_save(
                    lambda manager=manager, category=category: (
                        lifecycle._usage_snapshot(manager, category)
                    ),
                    lifecycle._USAGE_SAVE_DELAY_SECONDS,
                )
                return
            except Exception:
                _LOGGER.exception(
                    "Unable to schedule lazy usage %s; falling back to existing persistence",
                    label,
                )
        await previous_save_safely(manager, label, save)

    async def prune_if_due_off_path(manager: Any) -> None:
        today = dt_util.utcnow().date().isoformat()
        if getattr(manager, lifecycle._LAST_USAGE_PRUNE_DATE, None) == today:
            return
        current = getattr(manager, _USAGE_PRUNE_TASK, None)
        if isinstance(current, asyncio.Task) and not current.done():
            return

        async def run() -> None:
            try:
                await previous_prune_if_due(manager)
            except Exception:
                _LOGGER.exception("Background usage retention maintenance failed")

        task = asyncio.create_task(
            run(), name="extended_openai_usage_retention_maintenance"
        )
        setattr(manager, _USAGE_PRUNE_TASK, task)

        def done(completed: asyncio.Task[Any]) -> None:
            if getattr(manager, _USAGE_PRUNE_TASK, None) is completed:
                setattr(manager, _USAGE_PRUNE_TASK, None)

        task.add_done_callback(done)

    manager_type._async_save_safely = async_save_safely
    # The lifecycle finalizer resolves this module global at runtime. Replacing the
    # helper keeps its established call site while making the daily O(N) scan a
    # background maintenance task rather than part of response completion. If a
    # later hardening layer already made pruning persist-first and backgrounded it,
    # preserve that single task instead of scheduling an outer task around it.
    if not transactional_prune:
        lifecycle._async_prune_usage_if_due = prune_if_due_off_path


def _debug_event_has_text(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    event_type = str(data.get("type", ""))
    if "output_text.delta" in event_type and data.get("delta"):
        return True
    choices = data.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and delta.get("content"):
                return True
    return False


def _debug_event_has_action(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    event_type = str(data.get("type", ""))
    if "function_call" in event_type or "web_search_call" in event_type:
        return True
    item = data.get("item")
    return isinstance(item, dict) and item.get("type") in {
        "function_call",
        "web_search_call",
    }


def _debug_usage(data: Any) -> dict[str, int] | None:
    if not isinstance(data, dict):
        return None
    usage = data.get("usage")
    response = data.get("response")
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if not isinstance(usage, dict):
        return None

    def integer(value: Any) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    input_tokens = integer(usage.get("input_tokens")) or integer(
        usage.get("prompt_tokens")
    )
    output_tokens = integer(usage.get("output_tokens")) or integer(
        usage.get("completion_tokens")
    )
    total_tokens = integer(usage.get("total_tokens")) or input_tokens + output_tokens
    input_details = usage.get("input_tokens_details") or usage.get(
        "prompt_tokens_details"
    )
    output_details = usage.get("output_tokens_details") or usage.get(
        "completion_tokens_details"
    )
    cached = (
        integer(input_details.get("cached_tokens"))
        if isinstance(input_details, dict)
        else 0
    )
    reasoning = (
        integer(output_details.get("reasoning_tokens"))
        if isinstance(output_details, dict)
        else 0
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached,
        "reasoning_tokens": reasoning,
    }


def _install_debug_single_conversion() -> None:
    """Convert each provider debug event once instead of repeatedly walking it."""
    from . import debug

    request_type: Any = debug.DebugProviderRequest

    def add_event(request: Any, event: Any) -> None:
        now_ms = int((time.monotonic() - request._started_monotonic) * 1000)
        if request.first_event_ms is None:
            request.first_event_ms = now_ms

        serialized = debug._jsonable(event)
        if request.first_text_ms is None and _debug_event_has_text(serialized):
            request.first_text_ms = now_ms
        if request.first_action_ms is None and _debug_event_has_action(serialized):
            request.first_action_ms = now_ms
        if usage := _debug_usage(serialized):
            request.usage = usage
        if request.response_events_truncated:
            return

        # ``serialized`` is already bounded and JSON-safe, so do not recursively
        # normalize it again merely to determine its retained size.
        event_size = len(
            json.dumps(
                serialized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if request._event_bytes + event_size > debug.DEBUG_MAX_EVENT_BYTES:
            request.response_events_truncated = True
            return
        request.response_events.append(serialized)
        request._event_bytes += event_size

    request_type.add_event = add_event


def _install_broadcast_cold_path_guard() -> None:
    """Do not initialize Intercom for requests that cannot be targeted broadcasts."""
    from . import local_intents

    original = local_intents._async_try_targeted_broadcast

    async def try_targeted_broadcast(hass: Any, user_input: Any) -> Any:
        text = getattr(user_input, "text", None)
        if (
            not isinstance(text, str)
            or not text.strip()
            or not local_intents.is_targeted_broadcast_request(text)
        ):
            return None
        return await original(hass, user_input)

    local_intents._async_try_targeted_broadcast = try_targeted_broadcast


def _install_request_rule_two_pass_matching() -> None:
    """Skip fuzzy scoring whenever deterministic Request Rule matching succeeds."""
    from . import request_rules as rules

    rank = {
        "equals": 5,
        "sentence_pattern": 4,
        "starts_with": 3,
        "ends_with": 2,
        "contains": 1,
    }

    def match(manager: Any, text: str) -> Any:
        normalized_candidates: dict[tuple[bool, bool], str] = {}

        def candidate(settings: dict[str, Any]) -> str:
            key = (
                bool(settings.get("word_forms")),
                bool(settings.get("wording_alternatives")),
            )
            try:
                return normalized_candidates[key]
            except KeyError:
                value = rules.normalize_text(text, settings, manager._wording_groups)
                normalized_candidates[key] = value
                return value

        deterministic: list[tuple[tuple[int, int, int], Any]] = []
        for rule, settings, phrases in manager._compiled:
            normalized = (
                "" if rule["match_type"] == "sentence_pattern" else candidate(settings)
            )
            for compiled in phrases:
                if compiled.sentence is not None:
                    slots = rules._match_compiled_sentence(compiled, text)
                    if slots is None:
                        continue
                    result = rules.RuleMatch(
                        rule, compiled.original, False, 100.0, slots
                    )
                    deterministic.append(
                        (
                            (
                                rank[rule["match_type"]],
                                len(compiled.original),
                                -rule["order"],
                            ),
                            result,
                        )
                    )
                    continue

                phrase = cast(str, compiled.normalized)
                if rules._deterministic_match(normalized, phrase, rule["match_type"]):
                    result = rules.RuleMatch(rule, compiled.original, False, 100.0)
                    deterministic.append(
                        (
                            (rank[rule["match_type"]], len(phrase), -rule["order"]),
                            result,
                        )
                    )

        if deterministic:
            return max(deterministic, key=lambda item: item[0])[1]

        fuzzy: list[tuple[tuple[float, int, int], Any]] = []
        for rule, settings, phrases in manager._compiled:
            if rule["match_type"] == "sentence_pattern" or not settings["fuzzy"]:
                continue
            normalized = candidate(settings)
            for compiled in phrases:
                if compiled.sentence is not None:
                    continue
                phrase = cast(str, compiled.normalized)
                score = rules._fuzzy_score(normalized, phrase, rule["match_type"])
                if score >= settings["fuzzy_threshold"]:
                    result = rules.RuleMatch(rule, compiled.original, True, score)
                    fuzzy.append(
                        ((score, rank[rule["match_type"]], -rule["order"]), result)
                    )
        if fuzzy:
            return max(fuzzy, key=lambda item: item[0])[1]
        return None

    manager_type: Any = rules.RequestRules
    manager_type.match = match
