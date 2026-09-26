from __future__ import annotations

from ci import live_openai_catalog_conformance as conformance
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_REASONING_EFFORT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_WEB_SEARCH,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    BUNDLED_CATALOG,
)


def _model(model_id: str) -> dict:
    return BUNDLED_CATALOG.resolved[model_id]


def test_assertion_mode_covers_every_api_specific_effort() -> None:
    model = _model("gpt-5.6-luna")
    cases = conformance._assertion_cases_for_model(
        model,
        include_service_tiers=False,
    )

    chat_efforts = {
        case.options.get(CONF_REASONING_EFFORT)
        for case in cases
        if case.options["api_mode"] == API_MODE_CHAT_COMPLETIONS
        and "conformance:api_reasoning" in case.coverage
    }
    response_efforts = {
        case.options.get(CONF_REASONING_EFFORT)
        for case in cases
        if case.options["api_mode"] == API_MODE_RESPONSES
        and "conformance:api_reasoning" in case.coverage
    }

    assert chat_efforts == {"none", "low", "medium", "high", "xhigh"}
    assert response_efforts == {"none", "low", "medium", "high", "xhigh", "max"}


def test_assertion_mode_covers_declared_sampling_and_web_search() -> None:
    model = _model("gpt-4.1-mini")
    cases = conformance._assertion_cases_for_model(
        model,
        include_service_tiers=False,
    )

    assert any(CONF_TEMPERATURE in case.options for case in cases)
    assert any(CONF_TOP_P in case.options for case in cases)
    assert any(case.options.get(CONF_WEB_SEARCH) is True for case in cases)


def test_cartesian_mode_combines_independent_capabilities() -> None:
    model = _model("gpt-4.1-mini")
    cases = conformance._cartesian_cases_for_model(
        model,
        include_service_tiers=False,
    )

    assert any(
        case.options.get(CONF_TEMPERATURE) is not None
        and case.options.get(CONF_TOP_P) is not None
        and "function_tools" in case.coverage
        and case.options.get(CONF_WEB_SEARCH) is True
        for case in cases
    )


def test_cartesian_mode_respects_conditional_chat_function_rules() -> None:
    model = _model("gpt-5.6-luna")
    cases = conformance._cartesian_cases_for_model(
        model,
        include_service_tiers=False,
    )

    chat_tool_cases = [
        case
        for case in cases
        if case.options["api_mode"] == API_MODE_CHAT_COMPLETIONS
        and "function_tools" in case.coverage
    ]

    assert chat_tool_cases
    assert all(
        case.options.get(CONF_REASONING_EFFORT) == "none"
        for case in chat_tool_cases
    )


def test_service_tiers_are_opt_in_dimension() -> None:
    model = _model("gpt-4.1-mini")
    without = conformance._cartesian_cases_for_model(
        model,
        include_service_tiers=False,
    )
    with_tiers = conformance._cartesian_cases_for_model(
        model,
        include_service_tiers=True,
    )

    assert all("service_tier" not in case.options for case in without)
    assert any("service_tier" in case.options for case in with_tiers)
    assert len(with_tiers) > len(without)


def test_model_filter_is_substring_based() -> None:
    models = conformance._selected_models(
        include_expensive=False,
        model_filter="gpt-6",
    )

    assert models
    assert all("gpt-6" in model["id"] for model in models)
