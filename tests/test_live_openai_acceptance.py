from __future__ import annotations

from datetime import datetime, timezone
import random

from ci import live_openai_acceptance as live
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_WEB_SEARCH,
)


def _model() -> dict:
    return {
        "id": "synthetic-model",
        "status": "current",
        "api": {"responses": True, "chat_completions": True},
        "reasoning": {"supported": True, "efforts": ["none", "low"]},
        "temperature": {
            "support": "conditional",
            "allowed_reasoning_efforts": ["none"],
        },
        "top_p": {
            "support": "conditional",
            "allowed_reasoning_efforts": ["none"],
        },
        "function_calling": {
            "responses": True,
            "chat_completions": {
                "support": "conditional",
                "allowed_reasoning_efforts": ["none"],
            },
        },
        "responses_web_search": True,
        "structured_outputs": True,
        "service_tiers": ["auto", "default", "flex"],
    }


def test_candidate_pool_includes_catalogued_capabilities() -> None:
    candidates = live._candidate_cases(random.Random(42), _model())

    assert any(case.options.get(CONF_TEMPERATURE) is not None for case in candidates)
    assert any(case.options.get(CONF_TOP_P) is not None for case in candidates)
    assert any(case.options.get(CONF_WEB_SEARCH) is True for case in candidates)
    assert any("reasoning:none" in case.coverage for case in candidates)
    assert any("reasoning:low" in case.coverage for case in candidates)
    assert any("function_tools" in case.coverage for case in candidates)
    assert any("api:responses" in case.coverage for case in candidates)


def test_sampling_is_only_generated_for_allowed_reasoning_effort() -> None:
    candidates = live._candidate_cases(random.Random(7), _model())

    sampling = [
        case
        for case in candidates
        if CONF_TEMPERATURE in case.options or CONF_TOP_P in case.options
    ]
    assert sampling
    assert all(case.options.get("reasoning_effort") == "none" for case in sampling)


def test_web_search_is_only_generated_for_responses() -> None:
    candidates = live._candidate_cases(random.Random(7), _model())

    web_cases = [case for case in candidates if case.options.get(CONF_WEB_SEARCH)]
    assert web_cases
    assert all(case.options["api_mode"] == API_MODE_RESPONSES for case in web_cases)


def test_case_budget_does_not_change() -> None:
    model = _model()
    models = [model, {**model, "id": "synthetic-model-2"}]

    cases = live._cases(
        random.Random(123),
        models,
        2,
        history=live._empty_history(),
        now=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )

    assert len(cases) == 4


def test_history_biases_untested_capability_without_making_it_mandatory() -> None:
    case = live.ProbeCase(
        model="synthetic-model",
        profile="context-heavy",
        options={},
        coverage=("api:responses", "web_search"),
    )
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    history = {
        "schema_version": live.HISTORY_SCHEMA_VERSION,
        "models": {
            "synthetic-model": {
                "capabilities": {
                    "api:responses": {
                        "count": 25,
                        "last_tested": "2026-09-26T00:00:00+00:00",
                        "last_status": "passed",
                    }
                }
            }
        },
    }

    weighted = live._candidate_weight(history, case, now)
    fully_fresh = live._candidate_weight(live._empty_history(), case, now)

    assert weighted > 0
    assert fully_fresh > weighted
