"""Manual live OpenAI compatibility probes for EOAI.

This module is intentionally not part of ordinary pytest collection. It uses a real
OpenAI API key and must only run from the protected manual workflow.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
from typing import Any

from openai import AsyncOpenAI

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SERVICE_TIER,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_WEB_SEARCH,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    knowledge_tools,
)
from custom_components.extended_openai_conversation_responses.memory import memory_tools
from custom_components.extended_openai_conversation_responses.model_catalog import (
    BUNDLED_CATALOG,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
    format_function_tools,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    temporary_memory_tools,
)

DEFAULT_MODEL_COUNT = 6
DEFAULT_CASES_PER_MODEL = 2
MAX_MODEL_COUNT = 12
MAX_CASES_PER_MODEL = 4
MAX_OUTPUT_TOKENS = 64
HISTORY_SCHEMA_VERSION = 1
_SAMPLING_VALUES = (0.2, 0.7, 1.0)
_PROFILES = ("minimal", "context-heavy", "tool-heavy", "kitchen-sink")


@dataclass(frozen=True, slots=True)
class ProbeCase:
    model: str
    profile: str
    options: dict[str, Any]
    coverage: tuple[str, ...]


def _feature_tags(model: dict[str, Any]) -> set[str]:
    tags: set[str] = set()
    if model["reasoning"]["supported"]:
        tags.add("reasoning")
    else:
        tags.add("non-reasoning")
    if model["api"]["responses"]:
        tags.add("responses")
    if model["api"]["chat_completions"]:
        tags.add("chat")
    if model["api"]["responses"] and not model["api"]["chat_completions"]:
        tags.add("responses-only")
    if any(
        bool(model["function_calling"].get(api))
        for api in ("responses", "chat_completions")
    ):
        tags.add("functions")
    if model.get("structured_outputs"):
        tags.add("structured")
    if model.get("responses_web_search"):
        tags.add("web-search")
    if model["temperature"]["support"] in {"always", "conditional"}:
        tags.add("temperature")
    if model["top_p"]["support"] in {"always", "conditional"}:
        tags.add("top-p")
    return tags


def _empty_history() -> dict[str, Any]:
    return {"schema_version": HISTORY_SCHEMA_VERSION, "models": {}}


def _load_history(path: str | None) -> dict[str, Any]:
    if not path:
        return _empty_history()
    history_path = Path(path)
    if not history_path.exists():
        return _empty_history()
    try:
        value = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise SystemExit(f"Unable to read live OpenAI coverage history: {err}") from err
    if not isinstance(value, dict) or value.get("schema_version") != HISTORY_SCHEMA_VERSION:
        raise SystemExit("Unsupported live OpenAI coverage history schema")
    if not isinstance(value.get("models"), dict):
        raise SystemExit("Invalid live OpenAI coverage history")
    return value


def _history_item(history: dict[str, Any], model: str, key: str) -> dict[str, Any]:
    model_history = history.get("models", {}).get(model, {})
    capabilities = model_history.get("capabilities", {})
    item = capabilities.get(key, {})
    return item if isinstance(item, dict) else {}


def _case_history_item(history: dict[str, Any], model: str) -> dict[str, Any]:
    model_history = history.get("models", {}).get(model, {})
    item = model_history.get("cases", {})
    return item if isinstance(item, dict) else {}


def _age_days(item: dict[str, Any], now: datetime) -> float:
    raw = item.get("last_tested")
    if not isinstance(raw, str):
        return 365.0
    try:
        tested = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 365.0
    if tested.tzinfo is None:
        tested = tested.replace(tzinfo=timezone.utc)
    return max(0.0, (now - tested).total_seconds() / 86400.0)


def _coverage_weight(item: dict[str, Any], now: datetime) -> float:
    count = item.get("count", 0)
    if type(count) is not int or count < 0:
        count = 0
    frequency = 1.0 / math.sqrt(count + 1.0)
    recency = 1.0 + min(_age_days(item, now) / 30.0, 4.0)
    return frequency * recency


def _weighted_pick_index(rng: random.Random, weights: list[float]) -> int:
    total = sum(max(weight, 0.0) for weight in weights)
    if total <= 0:
        return rng.randrange(len(weights))
    point = rng.random() * total
    upto = 0.0
    for index, weight in enumerate(weights):
        upto += max(weight, 0.0)
        if point <= upto:
            return index
    return len(weights) - 1


def _select_models(
    rng: random.Random,
    *,
    count: int,
    include_expensive: bool,
    history: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    current = [
        item
        for item in BUNDLED_CATALOG.resolved.values()
        if item["status"] == "current"
        and (item["api"]["responses"] or item["api"]["chat_completions"])
    ]
    if not include_expensive:
        filtered = [item for item in current if "-pro" not in item["id"]]
        if filtered:
            current = filtered

    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    while current and len(selected) < count:
        weights: list[float] = []
        for model in current:
            history_weight = _coverage_weight(
                _case_history_item(history, model["id"]), now
            )
            novelty = len(_feature_tags(model) - covered)
            weights.append(history_weight * (1.0 + 0.18 * novelty))
        index = _weighted_pick_index(rng, weights)
        choice = current.pop(index)
        selected.append(choice)
        covered |= _feature_tags(choice)
    return selected


def _supports_sampling(
    model: dict[str, Any], parameter: str, effort: str | None
) -> bool:
    metadata = model[parameter]
    support = metadata["support"]
    if support == "always":
        return True
    if support == "conditional":
        return effort in metadata["allowed_reasoning_efforts"]
    return False


def _function_calling_allowed(
    model: dict[str, Any],
    api_mode: str,
    effort: str | None,
) -> bool:
    support = model["function_calling"][api_mode]
    if support is True:
        return True
    if support is False:
        return False
    return (
        support.get("support") == "conditional"
        and effort in support.get("allowed_reasoning_efforts", [])
    )


def _viable_api_efforts(model: dict[str, Any]) -> list[tuple[str, str | None]]:
    efforts: list[str | None] = list(model["reasoning"]["efforts"]) or [None]
    result: list[tuple[str, str | None]] = []
    for api_mode in (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS):
        if not model["api"].get(api_mode):
            continue
        result.extend((api_mode, effort) for effort in efforts)
    return result


def _base_options(
    model: dict[str, Any], api_mode: str, effort: str | None
) -> dict[str, Any]:
    options: dict[str, Any] = {
        CONF_CHAT_MODEL: model["id"],
        CONF_API_MODE: api_mode,
        CONF_MAX_TOKENS: MAX_OUTPUT_TOKENS,
    }
    if effort is not None:
        options[CONF_REASONING_EFFORT] = effort
    return options


def _coverage_for(
    api_mode: str,
    effort: str | None,
    *features: str,
) -> tuple[str, ...]:
    tags = [f"api:{api_mode}"]
    if effort is not None:
        tags.append(f"reasoning:{effort}")
    tags.extend(features)
    return tuple(tags)


def _candidate_cases(
    rng: random.Random, model: dict[str, Any]
) -> list[ProbeCase]:
    candidates: list[ProbeCase] = []
    safe_tiers = [
        tier for tier in model.get("service_tiers", []) if tier in {"auto", "default"}
    ]

    for api_mode, effort in _viable_api_efforts(model):
        base = _base_options(model, api_mode, effort)
        candidates.append(
            ProbeCase(
                model=model["id"],
                profile="minimal",
                options=dict(base),
                coverage=_coverage_for(api_mode, effort, "profile:minimal"),
            )
        )
        candidates.append(
            ProbeCase(
                model=model["id"],
                profile="context-heavy",
                options=dict(base),
                coverage=_coverage_for(api_mode, effort, "profile:context-heavy"),
            )
        )

        if _supports_sampling(model, CONF_TEMPERATURE, effort):
            options = dict(base)
            options[CONF_TEMPERATURE] = rng.choice(_SAMPLING_VALUES)
            candidates.append(
                ProbeCase(
                    model=model["id"],
                    profile="context-heavy",
                    options=options,
                    coverage=_coverage_for(api_mode, effort, "temperature"),
                )
            )

        if _supports_sampling(model, CONF_TOP_P, effort):
            options = dict(base)
            options[CONF_TOP_P] = rng.choice(_SAMPLING_VALUES)
            candidates.append(
                ProbeCase(
                    model=model["id"],
                    profile="context-heavy",
                    options=options,
                    coverage=_coverage_for(api_mode, effort, "top_p"),
                )
            )

        function_allowed = _function_calling_allowed(model, api_mode, effort)
        if function_allowed:
            candidates.append(
                ProbeCase(
                    model=model["id"],
                    profile="tool-heavy",
                    options=dict(base),
                    coverage=_coverage_for(api_mode, effort, "function_tools"),
                )
            )

        web_allowed = (
            api_mode == API_MODE_RESPONSES and model.get("responses_web_search", False)
        )
        if web_allowed:
            options = dict(base)
            options[CONF_WEB_SEARCH] = True
            candidates.append(
                ProbeCase(
                    model=model["id"],
                    profile="context-heavy",
                    options=options,
                    coverage=_coverage_for(api_mode, effort, "web_search"),
                )
            )
            if function_allowed:
                candidates.append(
                    ProbeCase(
                        model=model["id"],
                        profile="kitchen-sink",
                        options=options,
                        coverage=_coverage_for(
                            api_mode,
                            effort,
                            "web_search",
                            "function_tools",
                            "profile:kitchen-sink",
                        ),
                    )
                )

        for tier in safe_tiers:
            options = dict(base)
            options[CONF_SERVICE_TIER] = tier
            candidates.append(
                ProbeCase(
                    model=model["id"],
                    profile="minimal",
                    options=options,
                    coverage=_coverage_for(api_mode, effort, f"service_tier:{tier}"),
                )
            )

    return candidates


def _candidate_weight(
    history: dict[str, Any], case: ProbeCase, now: datetime
) -> float:
    weights = [
        _coverage_weight(_history_item(history, case.model, key), now)
        for key in case.coverage
    ]
    if not weights:
        return 1.0
    return max(weights) + 0.25 * (sum(weights) / len(weights))


def _cases(
    rng: random.Random,
    models: list[dict[str, Any]],
    cases_per_model: int,
    *,
    history: dict[str, Any],
    now: datetime,
) -> list[ProbeCase]:
    result: list[ProbeCase] = []
    for model in models:
        candidates = _candidate_cases(rng, model)
        for _ in range(cases_per_model):
            if not candidates:
                break
            weights = [_candidate_weight(history, case, now) for case in candidates]
            index = _weighted_pick_index(rng, weights)
            result.append(candidates.pop(index))
    return result


def _synthetic_function_tool() -> dict[str, Any]:
    return {
        "spec": {
            "name": "eoai_live_probe",
            "description": "Return synthetic compatibility data for EOAI live acceptance.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["summary", "detail"],
                    },
                    "entity_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "include_attributes": {"type": "boolean"},
                },
                "required": ["mode", "entity_ids", "include_attributes"],
                "additionalProperties": False,
            },
        },
        "function": {"type": "live_acceptance_probe"},
    }


def _tool_profile(profile: str) -> list[dict[str, Any]]:
    if profile not in {"tool-heavy", "kitchen-sink"}:
        return []
    # These are the production schemas EOAI exposes for the feature families most
    # likely to drift against provider-side function schema validation.
    tools = [
        _synthetic_function_tool(),
        *memory_tools(),
        *temporary_memory_tools(),
        *knowledge_tools(),
    ]
    # Keep live requests bounded while preserving representatives from every family.
    by_name = {tool["spec"]["name"]: tool for tool in tools}
    preferred = [
        "eoai_live_probe",
        "memory_search",
        "memory_add",
        "temporary_memory_search",
        "temporary_memory_add",
        "knowledge_search",
        "knowledge_get",
    ]
    selected = [by_name[name] for name in preferred if name in by_name]
    if len(selected) < 7:
        seen = {tool["spec"]["name"] for tool in selected}
        selected.extend(tool for tool in tools if tool["spec"]["name"] not in seen)
    return selected[:10]


def _system_prompt(profile: str) -> str:
    base = (
        "You are an EOAI live compatibility probe. Reply very briefly. "
        "The following content is synthetic test data, not user information."
    )
    if profile == "minimal":
        return base
    context = """
Custom prompt: Prefer concise answers and never invent Home Assistant state.
Skill instructions: A synthetic lighting skill says to inspect exposed state before acting.
Persistent memory: The household calls the synthetic test room 'Lab'.
Temporary memory: The user just asked about the synthetic desk lamp.
Knowledge excerpt: Lab operating hours are 09:00-17:00.
Exposed Home Assistant state:
- light.synthetic_desk = on
  brightness = 173
  color_temp_kelvin = 3200
  friendly_name = Synthetic Desk Lamp
- sensor.synthetic_unicode = "café ✓"
  numeric_value = 21.5
  nullable_value = null
  flags = ["alpha", "beta"]
"""
    return base + context


def _messages(
    profile: str, api_mode: str, *, web_search: bool = False
) -> list[dict[str, Any]]:
    system = _system_prompt(profile)
    if web_search:
        user = (
            "Use web search once to identify the current UTC date, then reply with "
            "only that date."
        )
    else:
        user = (
            "Confirm that you received the synthetic context. "
            "Do not call tools unless needed."
        )
    if api_mode == API_MODE_RESPONSES:
        return [
            {"type": "message", "role": "system", "content": system},
            {"type": "message", "role": "user", "content": user},
        ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def _consume(result: Any, streaming: bool) -> None:
    if not streaming:
        return
    async for _event in result:
        pass


async def _run_case(client: AsyncOpenAI, case: ProbeCase) -> dict[str, Any]:
    tools = _tool_profile(case.profile)
    web_search = bool(case.options.get(CONF_WEB_SEARCH))
    snapshot = build_provider_request_snapshot(
        case.options,
        {},
        tools_required=bool(tools) or web_search,
    )
    formatted_tools = format_function_tools(tools, snapshot.api_mode) if tools else []
    provider_tools = list(snapshot.provider_tools)
    all_tools = [*provider_tools, *formatted_tools]
    kwargs = dict(snapshot.api_kwargs)
    tool_kwargs = {"tools": all_tools} if all_tools else {}
    streaming = bool(kwargs.get("stream", False))

    if snapshot.api_mode == API_MODE_RESPONSES:
        result = await client.responses.create(
            input=_messages(
                case.profile,
                snapshot.api_mode,
                web_search=web_search,
            ),
            **kwargs,
            **tool_kwargs,
        )
    elif snapshot.api_mode == API_MODE_CHAT_COMPLETIONS:
        result = await client.chat.completions.create(
            messages=_messages(case.profile, snapshot.api_mode),
            **kwargs,
            **tool_kwargs,
        )
    else:
        raise AssertionError(f"Unexpected API mode: {snapshot.api_mode}")

    await _consume(result, streaming)
    if streaming:
        close = getattr(result, "close", None)
        if close is not None:
            await close()

    return {
        "model": case.model,
        "profile": case.profile,
        "coverage": list(case.coverage),
        "api_mode": snapshot.api_mode,
        "reasoning_effort": case.options.get(CONF_REASONING_EFFORT),
        "temperature": case.options.get(CONF_TEMPERATURE),
        "top_p": case.options.get(CONF_TOP_P),
        "service_tier": case.options.get(CONF_SERVICE_TIER),
        "web_search": web_search,
        "tools": [tool["spec"]["name"] for tool in tools],
        "provider_tools": [tool.get("type") for tool in provider_tools],
        "status": "passed",
    }


def _record_case(
    history: dict[str, Any],
    case: ProbeCase,
    *,
    status: str,
    tested_at: str,
) -> None:
    models = history.setdefault("models", {})
    model_history = models.setdefault(case.model, {})

    case_item = model_history.setdefault("cases", {})
    case_item["count"] = int(case_item.get("count", 0)) + 1
    case_item["last_tested"] = tested_at
    case_item["last_status"] = status

    capabilities = model_history.setdefault("capabilities", {})
    for key in case.coverage:
        item = capabilities.setdefault(key, {})
        item["count"] = int(item.get("count", 0)) + 1
        item["last_tested"] = tested_at
        item["last_status"] = status


def _write_history(path: str | None, history: dict[str, Any]) -> None:
    if not path:
        return
    Path(path).write_text(
        json.dumps(history, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def _main(args: argparse.Namespace) -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required")

    model_count = max(1, min(args.model_count, MAX_MODEL_COUNT))
    cases_per_model = max(1, min(args.cases_per_model, MAX_CASES_PER_MODEL))
    seed = args.seed if args.seed is not None else int.from_bytes(os.urandom(8), "big")
    rng = random.Random(seed)
    history = _load_history(args.history)
    selection_time = datetime.now(timezone.utc)
    models = _select_models(
        rng,
        count=model_count,
        include_expensive=args.include_expensive_models,
        history=history,
        now=selection_time,
    )
    cases = _cases(
        rng,
        models,
        cases_per_model,
        history=history,
        now=selection_time,
    )

    print(f"EOAI live OpenAI acceptance seed: {seed}")
    print("Selected models: " + ", ".join(item["id"] for item in models))
    print(f"Planned live requests: {len(cases)}")
    for case in cases:
        print(
            f"  {case.model}: {', '.join(case.coverage)} / {case.profile}",
            flush=True,
        )

    client = AsyncOpenAI(api_key=api_key, max_retries=0, timeout=45.0)
    report: dict[str, Any] = {
        "seed": seed,
        "model_count": len(models),
        "cases_per_model": cases_per_model,
        "history_schema_version": HISTORY_SCHEMA_VERSION,
        "results": [],
    }
    failures = 0
    try:
        for index, case in enumerate(cases, start=1):
            print(
                f"[{index}/{len(cases)}] {case.model} / {case.profile} / "
                f"{', '.join(case.coverage)}",
                flush=True,
            )
            tested_at = datetime.now(timezone.utc).isoformat()
            try:
                item = await _run_case(client, case)
                status = "passed"
            except Exception as err:
                failures += 1
                status = "failed"
                item = {
                    "model": case.model,
                    "profile": case.profile,
                    "coverage": list(case.coverage),
                    "options": {
                        key: value
                        for key, value in case.options.items()
                        if key != "api_key"
                    },
                    "status": status,
                    "error_type": type(err).__name__,
                    "error": str(err),
                }
                print(
                    f"FAILED: {case.model} / {case.profile}: "
                    f"{type(err).__name__}: {err}",
                    flush=True,
                )
            _record_case(history, case, status=status, tested_at=tested_at)
            report["results"].append(item)
    finally:
        await client.close()
        _write_history(args.history_out, history)

    path = Path(args.report)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Report written to {path}")
    if args.history_out:
        print(f"Coverage history written to {args.history_out}")
    if failures:
        print(f"{failures} live compatibility probe(s) failed.")
        return 1
    print("All live compatibility probes passed.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-count", type=int, default=DEFAULT_MODEL_COUNT)
    parser.add_argument(
        "--cases-per-model",
        type=int,
        default=DEFAULT_CASES_PER_MODEL,
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument("--include-expensive-models", action="store_true")
    parser.add_argument("--report", default="live-openai-acceptance-report.json")
    parser.add_argument("--history")
    parser.add_argument("--history-out")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parser().parse_args())))
