"""Manual exhaustive OpenAI model-catalogue conformance probes for EOAI.

This is deliberately separate from the cheap randomized live acceptance workflow.
It reuses that workflow's production request path and can either cover each distinct
catalogue assertion/boundary once or enumerate every valid capability combination.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import random
from typing import Any

from openai import AsyncOpenAI, BadRequestError

from ci import live_openai_acceptance as live
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_REASONING_EFFORT,
    CONF_SERVICE_TIER,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_WEB_SEARCH,
)
from custom_components.extended_openai_conversation_responses.model_capabilities import (
    capability_allowed,
    model_capability_snapshot,
    reasoning_efforts_for_api,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    BUNDLED_CATALOG,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)

MODE_ASSERTIONS = "assertions"
MODE_CARTESIAN = "cartesian"
MODE_EXPLORATORY = "exploratory"
_MODES = (MODE_ASSERTIONS, MODE_CARTESIAN, MODE_EXPLORATORY)
_SAMPLE_VALUE = 0.7
_EXPLORATORY_SAMPLING_MODELS = (
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-5.5",
    "gpt-5.6",
)
_EXPLORATORY_SAMPLING_PARAMETERS = (CONF_TEMPERATURE, CONF_TOP_P)


def _selected_models(
    *,
    include_expensive: bool,
    model_filter: str | None,
) -> list[dict[str, Any]]:
    models = [
        item
        for item in BUNDLED_CATALOG.resolved.values()
        if item["status"] == "current"
        and (item["api"]["responses"] or item["api"]["chat_completions"])
    ]
    if not include_expensive:
        models = [item for item in models if "-pro" not in item["id"]]
    if model_filter:
        needle = model_filter.strip().lower()
        models = [item for item in models if needle in item["id"].lower()]
    return sorted(models, key=lambda item: item["id"])


def _efforts(model: dict[str, Any], api_mode: str) -> list[str | None]:
    with model_capability_snapshot(model["id"], model):
        efforts = reasoning_efforts_for_api(model["id"], api_mode)
    return list(efforts) if efforts else [None]


def _tool_allowed(
    model: dict[str, Any],
    tool: str,
    api_mode: str,
    effort: str | None,
    *,
    service_tier: str | None = None,
) -> bool:
    with model_capability_snapshot(model["id"], model):
        return capability_allowed(
            model["id"],
            tool,
            api_mode,
            effort=effort,
            service_tier=service_tier,
            streaming=model["streaming"],
        )


def _sampling_allowed(
    model: dict[str, Any],
    name: str,
    effort: str | None,
) -> bool:
    metadata = model[name]
    if metadata["support"] == "always":
        return True
    if metadata["support"] == "conditional":
        return effort in (metadata["allowed_reasoning_efforts"] or [])
    return False


def _options(
    model: dict[str, Any],
    api_mode: str,
    effort: str | None,
    *,
    temperature: bool = False,
    top_p: bool = False,
    web_search: bool = False,
    service_tier: str | None = None,
) -> dict[str, Any]:
    result = live._base_options(model, api_mode, effort)
    if temperature:
        result[CONF_TEMPERATURE] = _SAMPLE_VALUE
    if top_p:
        result[CONF_TOP_P] = _SAMPLE_VALUE
    if web_search:
        result[CONF_WEB_SEARCH] = True
    if service_tier is not None:
        result[CONF_SERVICE_TIER] = service_tier
    return result


def _case(
    model: dict[str, Any],
    api_mode: str,
    effort: str | None,
    *,
    temperature: bool = False,
    top_p: bool = False,
    function_tools: bool = False,
    web_search: bool = False,
    service_tier: str | None = None,
    label: str,
) -> live.ProbeCase:
    if function_tools and web_search:
        profile = "kitchen-sink"
    elif function_tools:
        profile = "tool-heavy"
    elif temperature or top_p or web_search:
        profile = "context-heavy"
    else:
        profile = "minimal"
    features = [f"conformance:{label}"]
    if temperature:
        features.append("temperature")
    if top_p:
        features.append("top_p")
    if function_tools:
        features.append("function_tools")
    if web_search:
        features.append("web_search")
    if service_tier is not None:
        features.append(f"service_tier:{service_tier}")
    return live.ProbeCase(
        model=model["id"],
        profile=profile,
        options=_options(
            model,
            api_mode,
            effort,
            temperature=temperature,
            top_p=top_p,
            web_search=web_search,
            service_tier=service_tier,
        ),
        coverage=live._coverage_for(api_mode, effort, *features),
    )


def _assertion_cases_for_model(
    model: dict[str, Any],
    *,
    include_service_tiers: bool,
) -> list[live.ProbeCase]:
    """Cover every distinct positive catalogue assertion/boundary at least once."""
    cases: list[live.ProbeCase] = []
    for api_mode in (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS):
        if not model["api"].get(api_mode):
            continue
        efforts = _efforts(model, api_mode)
        for effort in efforts:
            # Every API-specific reasoning effort gets a plain provider request.
            cases.append(
                _case(
                    model,
                    api_mode,
                    effort,
                    label="api_reasoning",
                )
            )

            if _sampling_allowed(model, "temperature", effort):
                cases.append(
                    _case(
                        model,
                        api_mode,
                        effort,
                        temperature=True,
                        label="temperature",
                    )
                )
            if _sampling_allowed(model, "top_p", effort):
                cases.append(
                    _case(
                        model,
                        api_mode,
                        effort,
                        top_p=True,
                        label="top_p",
                    )
                )

            if _tool_allowed(model, "function", api_mode, effort):
                cases.append(
                    _case(
                        model,
                        api_mode,
                        effort,
                        function_tools=True,
                        label="function_tools",
                    )
                )
            if _tool_allowed(model, "web_search", api_mode, effort):
                cases.append(
                    _case(
                        model,
                        api_mode,
                        effort,
                        web_search=True,
                        label="web_search",
                    )
                )

        if include_service_tiers:
            representative_effort = efforts[0]
            for tier in model.get("service_tiers", []):
                cases.append(
                    _case(
                        model,
                        api_mode,
                        representative_effort,
                        service_tier=tier,
                        label="service_tier",
                    )
                )
    return cases


def _cartesian_cases_for_model(
    model: dict[str, Any],
    *,
    include_service_tiers: bool,
) -> list[live.ProbeCase]:
    """Enumerate every valid discrete capability combination represented by EOAI."""
    cases: list[live.ProbeCase] = []
    for api_mode in (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS):
        if not model["api"].get(api_mode):
            continue
        for effort in _efforts(model, api_mode):
            temperature_values = [False, True] if _sampling_allowed(
                model, "temperature", effort
            ) else [False]
            top_p_values = [False, True] if _sampling_allowed(
                model, "top_p", effort
            ) else [False]

            tiers: list[str | None]
            if include_service_tiers and model.get("service_tiers"):
                tiers = [None, *model["service_tiers"]]
            else:
                tiers = [None]

            for tier in tiers:
                function_values = [
                    False,
                    True,
                ] if _tool_allowed(
                    model,
                    "function",
                    api_mode,
                    effort,
                    service_tier=tier,
                ) else [False]
                web_values = [
                    False,
                    True,
                ] if _tool_allowed(
                    model,
                    "web_search",
                    api_mode,
                    effort,
                    service_tier=tier,
                ) else [False]

                for temperature, top_p, function_tools, web_search in itertools.product(
                    temperature_values,
                    top_p_values,
                    function_values,
                    web_values,
                ):
                    cases.append(
                        _case(
                            model,
                            api_mode,
                            effort,
                            temperature=temperature,
                            top_p=top_p,
                            function_tools=function_tools,
                            web_search=web_search,
                            service_tier=tier,
                            label="cartesian",
                        )
                    )
    # Exact duplicates can occur when a model has degenerate dimensions.
    unique: dict[tuple[Any, ...], live.ProbeCase] = {}
    for case in cases:
        key = (
            case.model,
            case.profile,
            tuple(sorted(case.options.items(), key=lambda item: item[0])),
        )
        unique[key] = case
    return list(unique.values())


def _cases(
    models: list[dict[str, Any]],
    *,
    mode: str,
    include_service_tiers: bool,
) -> list[live.ProbeCase]:
    result: list[live.ProbeCase] = []
    for model in models:
        if mode == MODE_CARTESIAN:
            result.extend(
                _cartesian_cases_for_model(
                    model,
                    include_service_tiers=include_service_tiers,
                )
            )
        else:
            result.extend(
                _assertion_cases_for_model(
                    model,
                    include_service_tiers=include_service_tiers,
                )
            )
    return result



def _exploratory_sampling_cases(
    models: list[dict[str, Any]],
) -> list[tuple[live.ProbeCase, str]]:
    """Build the small, explicit underclaim probe set for undocumented sampling."""
    by_id = {model["id"]: model for model in models}
    result: list[tuple[live.ProbeCase, str]] = []
    for model_id in _EXPLORATORY_SAMPLING_MODELS:
        model = by_id.get(model_id)
        if model is None:
            continue
        if "none" not in _efforts(model, API_MODE_RESPONSES):
            continue
        for parameter in _EXPLORATORY_SAMPLING_PARAMETERS:
            options = live._base_options(model, API_MODE_RESPONSES, "none")
            options[parameter] = _SAMPLE_VALUE
            result.append(
                (
                    live.ProbeCase(
                        model=model_id,
                        profile="context-heavy",
                        options=options,
                        coverage=live._coverage_for(
                            API_MODE_RESPONSES,
                            "none",
                            "exploratory_sampling",
                            f"exploratory:{parameter}",
                        ),
                    ),
                    parameter,
                )
            )
    return result


async def _run_exploratory_sampling_case(
    client: AsyncOpenAI,
    case: live.ProbeCase,
    parameter: str,
) -> dict[str, Any]:
    """Force one undocumented sampling parameter through an EOAI-built request."""
    metadata = deepcopy(BUNDLED_CATALOG.resolved[case.model])
    metadata[parameter] = {
        "support": "conditional",
        "allowed_reasoning_efforts": ["none"],
        "send_policy": "omit_unless_configured",
    }
    snapshot = build_provider_request_snapshot(
        case.options,
        {},
        tools_required=False,
        model_capabilities=metadata,
    )
    kwargs = dict(snapshot.api_kwargs)
    if parameter not in kwargs:
        raise AssertionError(f"Exploratory probe did not emit {parameter}")
    streaming = bool(kwargs.get("stream", False))
    try:
        result = await client.responses.create(
            input=live._messages(case.profile, snapshot.api_mode),
            **kwargs,
        )
        await live._consume(result, streaming)
        if streaming:
            close = getattr(result, "close", None)
            if close is not None:
                await close()
    except BadRequestError as err:
        return {
            "model": case.model,
            "api_mode": snapshot.api_mode,
            "reasoning_effort": "none",
            "parameter": parameter,
            "value": _SAMPLE_VALUE,
            "outcome": "rejected",
            "error_type": type(err).__name__,
            "error": str(err),
        }
    return {
        "model": case.model,
        "api_mode": snapshot.api_mode,
        "reasoning_effort": "none",
        "parameter": parameter,
        "value": _SAMPLE_VALUE,
        "outcome": "accepted",
    }


def _estimate(cases: list[live.ProbeCase]) -> dict[str, Any]:
    by_model: dict[str, int] = {}
    by_api: dict[str, int] = {}
    for case in cases:
        by_model[case.model] = by_model.get(case.model, 0) + 1
        api_mode = str(case.options["api_mode"])
        by_api[api_mode] = by_api.get(api_mode, 0) + 1
    return {
        "requests": len(cases),
        "by_model": dict(sorted(by_model.items())),
        "by_api": dict(sorted(by_api.items())),
    }


async def _run(args: argparse.Namespace) -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and not args.plan_only:
        raise SystemExit("OPENAI_API_KEY is required")

    models = _selected_models(
        include_expensive=args.include_expensive_models,
        model_filter=args.model_filter,
    )
    if not models:
        raise SystemExit("No current catalogue models matched the requested scope")

    exploratory_only = args.mode == MODE_EXPLORATORY
    cases = (
        []
        if exploratory_only
        else _cases(
            models,
            mode=args.mode,
            include_service_tiers=args.include_service_tiers,
        )
    )
    exploratory = (
        _exploratory_sampling_cases(models)
        if exploratory_only or args.exploratory_sampling
        else []
    )
    estimate = _estimate(cases)
    total_requests = estimate["requests"] + len(exploratory)
    print(
        f"EOAI catalogue conformance: {len(models)} models, "
        f"{total_requests} planned live requests ({args.mode})."
    )
    for model, count in estimate["by_model"].items():
        print(f"  {model}: {count}")

    report: dict[str, Any] = {
        "mode": args.mode,
        "model_filter": args.model_filter,
        "include_expensive_models": args.include_expensive_models,
        "include_service_tiers": args.include_service_tiers,
        "exploratory_sampling": bool(exploratory),
        "exploratory_only": exploratory_only,
        "planned": {
            **estimate,
            "exploratory_sampling_requests": len(exploratory),
            "total_requests": total_requests,
        },
        "results": [],
        "exploratory_results": [],
    }

    if args.plan_only:
        Path(args.report).write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return 0

    history = live._load_history(args.history)
    client = AsyncOpenAI(api_key=api_key, max_retries=0, timeout=45.0)
    failures = 0
    try:
        for index, case in enumerate(cases, start=1):
            print(
                f"[{index}/{len(cases)}] {case.model} / "
                f"{', '.join(case.coverage)}",
                flush=True,
            )
            tested_at = datetime.now(timezone.utc).isoformat()
            try:
                item = await live._run_case(client, case)
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
                    f"FAILED: {case.model}: {type(err).__name__}: {err}",
                    flush=True,
                )
            live._record_case(
                history,
                case,
                status=status,
                tested_at=tested_at,
            )
            report["results"].append(item)

        for index, (case, parameter) in enumerate(exploratory, start=1):
            print(
                f"[exploratory {index}/{len(exploratory)}] {case.model} / "
                f"responses / reasoning:none / {parameter}",
                flush=True,
            )
            tested_at = datetime.now(timezone.utc).isoformat()
            try:
                item = await _run_exploratory_sampling_case(
                    client,
                    case,
                    parameter,
                )
                status = f"exploratory_{item['outcome']}"
            except Exception as err:
                failures += 1
                status = "failed"
                item = {
                    "model": case.model,
                    "api_mode": API_MODE_RESPONSES,
                    "reasoning_effort": "none",
                    "parameter": parameter,
                    "value": _SAMPLE_VALUE,
                    "outcome": "probe_error",
                    "error_type": type(err).__name__,
                    "error": str(err),
                }
                print(
                    f"FAILED exploratory probe: {case.model} / {parameter}: "
                    f"{type(err).__name__}: {err}",
                    flush=True,
                )
            live._record_case(
                history,
                case,
                status=status,
                tested_at=tested_at,
            )
            report["exploratory_results"].append(item)
    finally:
        await client.close()
        live._write_history(args.history_out, history)

    Path(args.report).write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Report written to {args.report}")
    if failures:
        print(f"{failures} catalogue conformance probe(s) failed.")
        return 1
    print("All catalogue conformance probes passed.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=_MODES, default=MODE_ASSERTIONS)
    parser.add_argument("--model-filter")
    parser.add_argument("--include-expensive-models", action="store_true")
    parser.add_argument("--include-service-tiers", action="store_true")
    parser.add_argument("--exploratory-sampling", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument(
        "--report",
        default="live-openai-catalog-conformance-report.json",
    )
    parser.add_argument("--history")
    parser.add_argument("--history-out")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_parser().parse_args())))
