"""Manual live OpenAI compatibility probes for EOAI.

This module is intentionally not part of ordinary pytest collection. It uses a real
OpenAI API key and must only run from the protected manual workflow.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
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

_PROFILES = ("minimal", "context-heavy", "tool-heavy", "kitchen-sink")


@dataclass(frozen=True, slots=True)
class ProbeCase:
    model: str
    profile: str
    options: dict[str, Any]


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
    if any(bool(model["function_calling"].get(api)) for api in ("responses", "chat_completions")):
        tags.add("functions")
    if model.get("structured_outputs"):
        tags.add("structured")
    return tags


def _select_models(
    rng: random.Random,
    *,
    count: int,
    include_expensive: bool,
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
    rng.shuffle(current)

    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    while current and len(selected) < count:
        # Greedily prefer a model that expands capability coverage, then retain
        # random tie-breaking from the shuffled candidate order.
        best_index = max(
            range(len(current)),
            key=lambda i: len(_feature_tags(current[i]) - covered),
        )
        choice = current.pop(best_index)
        selected.append(choice)
        covered |= _feature_tags(choice)
    return selected


def _supports_sampling(model: dict[str, Any], parameter: str, effort: str | None) -> bool:
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


def _viable_api_efforts(
    model: dict[str, Any],
    *,
    requires_tools: bool,
) -> list[tuple[str, str | None]]:
    efforts: list[str | None] = list(model["reasoning"]["efforts"]) or [None]
    result: list[tuple[str, str | None]] = []
    for api_mode in (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS):
        if not model["api"].get(api_mode):
            continue
        for effort in efforts:
            if requires_tools and not _function_calling_allowed(
                model, api_mode, effort
            ):
                continue
            result.append((api_mode, effort))
    return result


def _build_options(
    rng: random.Random,
    model: dict[str, Any],
    ordinal: int,
    *,
    requires_tools: bool,
) -> dict[str, Any]:
    viable = _viable_api_efforts(model, requires_tools=requires_tools)
    if not viable:
        raise ValueError(
            f"{model['id']} has no viable API/effort combination for "
            f"requires_tools={requires_tools}"
        )
    # Rotate deterministically through viable API/effort pairs before random
    # repetition. This makes multi-case runs cover both APIs where possible.
    api_mode, effort = viable[ordinal % len(viable)]

    options: dict[str, Any] = {
        CONF_CHAT_MODEL: model["id"],
        CONF_API_MODE: api_mode,
        CONF_MAX_TOKENS: MAX_OUTPUT_TOKENS,
    }

    if effort is not None:
        options[CONF_REASONING_EFFORT] = effort

    sampling_candidates = [
        parameter
        for parameter in (CONF_TEMPERATURE, CONF_TOP_P)
        if _supports_sampling(model, parameter, effort)
    ]
    if sampling_candidates and ordinal % 2 == 1:
        parameter = rng.choice(sampling_candidates)
        options[parameter] = rng.choice((0.2, 0.7, 1.0))

    safe_tiers = [
        tier for tier in model.get("service_tiers", []) if tier in {"auto", "default"}
    ]
    if safe_tiers and ordinal % 3 == 1:
        options[CONF_SERVICE_TIER] = rng.choice(safe_tiers)

    return options


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


def _messages(profile: str, api_mode: str) -> list[dict[str, Any]]:
    system = _system_prompt(profile)
    user = (
        "Confirm that you received the synthetic context. Do not call tools unless needed."
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
    snapshot = build_provider_request_snapshot(
        case.options,
        {},
        tools_required=bool(tools),
    )
    formatted_tools = format_function_tools(tools, snapshot.api_mode) if tools else []
    kwargs = dict(snapshot.api_kwargs)
    tool_kwargs = {"tools": formatted_tools} if formatted_tools else {}
    streaming = bool(kwargs.get("stream", False))

    if snapshot.api_mode == API_MODE_RESPONSES:
        result = await client.responses.create(
            input=_messages(case.profile, snapshot.api_mode),
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
        "api_mode": snapshot.api_mode,
        "reasoning_effort": case.options.get(CONF_REASONING_EFFORT),
        "temperature": case.options.get(CONF_TEMPERATURE),
        "top_p": case.options.get(CONF_TOP_P),
        "service_tier": case.options.get(CONF_SERVICE_TIER),
        "tools": [tool["spec"]["name"] for tool in tools],
        "status": "passed",
    }


def _cases(
    rng: random.Random,
    models: list[dict[str, Any]],
    cases_per_model: int,
) -> list[ProbeCase]:
    result: list[ProbeCase] = []
    profile_offset = rng.randrange(len(_PROFILES))
    for model_index, model in enumerate(models):
        for ordinal in range(cases_per_model):
            profile = _PROFILES[
                (profile_offset + model_index + ordinal) % len(_PROFILES)
            ]
            requires_tools = profile in {"tool-heavy", "kitchen-sink"}
            if requires_tools and not _viable_api_efforts(
                model, requires_tools=True
            ):
                profile = "context-heavy"
                requires_tools = False
            result.append(
                ProbeCase(
                    model=model["id"],
                    profile=profile,
                    options=_build_options(
                        rng,
                        model,
                        ordinal,
                        requires_tools=requires_tools,
                    ),
                )
            )
    return result


async def _main(args: argparse.Namespace) -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required")

    model_count = max(1, min(args.model_count, MAX_MODEL_COUNT))
    cases_per_model = max(1, min(args.cases_per_model, MAX_CASES_PER_MODEL))
    seed = args.seed if args.seed is not None else int.from_bytes(os.urandom(8), "big")
    rng = random.Random(seed)
    models = _select_models(
        rng,
        count=model_count,
        include_expensive=args.include_expensive_models,
    )
    cases = _cases(rng, models, cases_per_model)

    print(f"EOAI live OpenAI acceptance seed: {seed}")
    print("Selected models: " + ", ".join(item["id"] for item in models))
    print(f"Planned live requests: {len(cases)}")

    client = AsyncOpenAI(api_key=api_key, max_retries=0, timeout=45.0)
    report: dict[str, Any] = {
        "seed": seed,
        "model_count": len(models),
        "cases_per_model": cases_per_model,
        "results": [],
    }
    failures = 0
    try:
        for index, case in enumerate(cases, start=1):
            print(
                f"[{index}/{len(cases)}] {case.model} / {case.profile}",
                flush=True,
            )
            try:
                item = await _run_case(client, case)
            except Exception as err:  # The report must retain the exact provider failure.
                failures += 1
                item = {
                    "model": case.model,
                    "profile": case.profile,
                    "options": {
                        key: value
                        for key, value in case.options.items()
                        if key != "api_key"
                    },
                    "status": "failed",
                    "error_type": type(err).__name__,
                    "error": str(err),
                }
                print(
                    f"FAILED: {case.model} / {case.profile}: "
                    f"{type(err).__name__}: {err}",
                    flush=True,
                )
            report["results"].append(item)
    finally:
        await client.close()

    path = Path(args.report)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Report written to {path}")
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
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parser().parse_args())))
