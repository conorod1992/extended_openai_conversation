"""Sampling types stay numeric across validation and provider projections."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import agent_config
from custom_components.extended_openai_conversation_responses.management_ui import (
    _parse_import_document,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)


@pytest.mark.parametrize("parameter", ["temperature", "top_p"])
@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("boundary", ["normalize", "merge", "persisted", "import"])
def test_boolean_sampling_is_rejected_at_configuration_boundaries(
    parameter, value, boundary
):
    raw = {"functions": "[]", parameter: value}
    original = deepcopy(raw)
    with pytest.raises(
        agent_config.AgentConfigError, match=rf"{parameter}: must be a int or float"
    ) as raised:
        if boundary == "normalize":
            agent_config.normalize_agent_config(raw)
        elif boundary == "merge":
            agent_config.merge_agent_config({"functions": "[]"}, {parameter: value})
        elif boundary == "persisted":
            agent_config.normalize_persisted_agent_config(raw)
        else:
            _parse_import_document(
                {
                    "schema": "extended_openai_conversation.agent",
                    "version": 2,
                    "config": raw,
                }
            )
    assert raised.value.field == parameter
    assert raw == original


@pytest.mark.parametrize("parameter", ["temperature", "top_p"])
@pytest.mark.parametrize("value", [0, 1, 0.0, 1.0, 0.25, "0", " 0.25 ", "1"])
@pytest.mark.parametrize("api", ["chat_completions", "responses"])
def test_numeric_sampling_normalization_and_payload(parameter, value, api):
    raw = {
        "chat_model": "gpt-4.1",
        "api_mode": api,
        "functions": "[]",
        parameter: value,
    }
    original = deepcopy(raw)
    normalized = agent_config.normalize_agent_config(raw)
    snapshot = build_provider_request_snapshot(raw, {}, tools_required=False)
    expected = float(value) if isinstance(value, str) else value
    assert normalized[parameter] == expected
    assert type(normalized[parameter]) is type(expected)
    assert snapshot.api_kwargs[parameter] == expected
    assert type(snapshot.api_kwargs[parameter]) is type(expected)
    assert raw == original


@pytest.mark.parametrize("parameter", ["temperature", "top_p"])
@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("api", ["chat_completions", "responses"])
def test_persisted_boolean_sampling_cannot_enter_provider_payload(
    parameter, value, api
):
    raw = {"chat_model": "gpt-4.1", "api_mode": api, parameter: value}
    original = deepcopy(raw)
    with pytest.raises(
        agent_config.AgentConfigError, match=rf"{parameter}:.*int or float"
    ):
        build_provider_request_snapshot(raw, {}, tools_required=False)
    assert raw == original


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
@pytest.mark.parametrize("model,effort", [("gpt-5", "minimal"), ("gpt-5.4", "high")])
@pytest.mark.parametrize("value", [True, False, None, 0, "0.25"])
def test_unsupported_sampling_keeps_existing_omission(model, effort, value, api):
    raw = {
        "chat_model": model,
        "reasoning_effort": effort,
        "api_mode": api,
        "temperature": value,
        "top_p": value,
    }
    snapshot = build_provider_request_snapshot(raw, {}, tools_required=False)
    assert "temperature" not in snapshot.api_kwargs
    assert "top_p" not in snapshot.api_kwargs
    assert raw["temperature"] is value
    assert raw["top_p"] is value


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
@pytest.mark.parametrize("sampling", [{}, {"temperature": None, "top_p": None}])
def test_unset_sampling_stays_omitted(api, sampling):
    snapshot = build_provider_request_snapshot(
        {"chat_model": "gpt-4.1", "api_mode": api, **sampling},
        {},
        tools_required=False,
    )
    assert "temperature" not in snapshot.api_kwargs
    assert "top_p" not in snapshot.api_kwargs


@pytest.mark.parametrize("expected", [int, (int, float), (float, int)])
@pytest.mark.parametrize("value", [True, False])
def test_numeric_type_contract_excludes_boolean_subclasses(expected, value):
    with pytest.raises(agent_config.AgentConfigError, match="number: must be"):
        agent_config._require_type({"number": value}, ("number",), expected)


@pytest.mark.parametrize("expected", [bool, (int, bool)])
@pytest.mark.parametrize("value", [True, False])
def test_explicit_boolean_type_contract_is_preserved(expected, value):
    agent_config._require_type({"flag": value}, ("flag",), expected)
