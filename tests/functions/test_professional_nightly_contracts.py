"""Independent nightly contracts for generated configuration and Function schemas.

These tests deliberately keep their expected results outside the production generators.
They live under tests/functions so the existing Enhanced Functions campaign collects
them without adding another expensive workflow lane.
"""

from __future__ import annotations

from copy import deepcopy
import json
import random

import pytest

from custom_components.extended_openai_conversation_responses import agent_config
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
    validate_function_schema,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)


def _spec(schema: dict) -> dict:
    return {"name": "nightly_generated_contract", "parameters": schema}


def _independent_scalar(kind: str, raw):
    """Small independent oracle for EOAI's documented safe scalar coercions."""
    if kind == "string":
        assert isinstance(raw, str)
        return raw
    if kind == "integer":
        assert not isinstance(raw, bool)
        return int(raw.strip()) if isinstance(raw, str) else raw
    if kind == "number":
        assert not isinstance(raw, bool)
        return float(raw.strip()) if isinstance(raw, str) else raw
    if kind == "boolean":
        if isinstance(raw, str):
            folded = raw.casefold()
            assert folded in {"true", "false"}
            return folded == "true"
        assert isinstance(raw, bool)
        return raw
    raise AssertionError(kind)


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        (
            {
                "chat_model": "gpt-5.6",
                "api_mode": "chat_completions",
                "reasoning_effort": "none",
                "max_tokens": "750",
                "temperature": "0.5",
                "functions": [],
            },
            {
                "chat_model": "gpt-5.6",
                "api_mode": "chat_completions",
                "reasoning_effort": "none",
                "max_tokens": 750,
                "temperature": 0.5,
            },
        ),
        (
            {
                "chat_model": "gpt-5-mini",
                "api_mode": "responses",
                "reasoning_effort": "low",
                "memory_mode": "manual",
                "knowledge_enabled": True,
                "archive_enabled": True,
                "functions": [],
            },
            {
                "chat_model": "gpt-5-mini",
                "api_mode": "responses",
                "reasoning_effort": "low",
                "memory_mode": "manual",
                "knowledge_enabled": True,
                "archive_enabled": True,
            },
        ),
    ],
    ids=["chat-completions-sampling", "responses-durable-features"],
)
def test_reviewed_configuration_witnesses_have_independent_expected_values(
    saved, expected
):
    """Reviewed witnesses must not disappear because a generator filters them out."""
    normalized = agent_config.normalize_agent_config(saved)
    for key, value in expected.items():
        assert normalized[key] == value
    request = build_provider_request_snapshot(normalized, {})
    assert request.api_mode == expected["api_mode"]
    assert request.api_kwargs["model"] == expected["chat_model"]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"max_tokens": 0}, "max_tokens"),
        ({"memory_auto_retrieve_limit": -1}, "memory_auto_retrieve_limit"),
        ({"conversation_timeout_minutes": 0}, "conversation_timeout_minutes"),
    ],
    ids=["max-tokens", "memory-retrieve-limit", "conversation-timeout"],
)
def test_reviewed_invalid_configuration_neighbours_fail_closed(payload, field):
    with pytest.raises(agent_config.AgentConfigError, match=field):
        agent_config.normalize_agent_config(payload)


def test_seeded_supported_schema_contracts_match_independent_oracle():
    """Generate supported schemas and compare production validation with our oracle."""
    rng = random.Random(0xE0A1C0DE)
    scalar_kinds = ("string", "integer", "number", "boolean")
    for case in range(80):
        fields = {}
        raw = {}
        expected = {}
        required = []
        for index in range(1 + rng.randrange(5)):
            kind = rng.choice(scalar_kinds)
            name = f"field_{case}_{index}"
            schema = {"type": kind}
            if kind == "string":
                schema |= {"minLength": 1, "maxLength": 40}
                value = f"value-{case}-{index}"
            elif kind == "integer":
                schema |= {"minimum": -20, "maximum": 20}
                numeric = rng.randrange(-20, 21)
                value = str(numeric) if rng.random() < 0.5 else numeric
            elif kind == "number":
                schema |= {"minimum": -20.5, "maximum": 20.5}
                numeric = round(rng.uniform(-20, 20), 3)
                value = str(numeric) if rng.random() < 0.5 else numeric
            else:
                value = rng.choice([True, False, "true", "false"])
            fields[name] = schema
            raw[name] = value
            expected[name] = _independent_scalar(kind, value)
            required.append(name)

        schema = {
            "type": "object",
            "properties": fields,
            "required": required,
            "additionalProperties": False,
        }
        assert validate_function_schema(schema) == ()
        assert validate_function_arguments(_spec(schema), raw) == expected


def test_generated_nested_container_contracts_preserve_values_and_constraints():
    schema = {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "settings": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "minimum": 0, "maximum": 5},
                    "enabled": {"type": "boolean"},
                },
                "required": ["count", "enabled"],
                "additionalProperties": False,
            },
            "literal": {
                "type": "object",
                "const": {"pattern": "business-value", "nested": {"type": "literal"}},
            },
        },
        "required": ["labels", "settings", "literal"],
        "additionalProperties": False,
    }
    raw = {
        "labels": ["alpha", "beta"],
        "settings": {"count": "2", "enabled": "false"},
        "literal": {"pattern": "business-value", "nested": {"type": "literal"}},
    }
    expected = {
        "labels": ["alpha", "beta"],
        "settings": {"count": 2, "enabled": False},
        "literal": deepcopy(raw["literal"]),
    }
    assert validate_function_schema(schema) == ()
    assert validate_function_arguments(_spec(schema), raw) == expected

    bad_cases = [
        {**raw, "labels": ["alpha", "alpha"]},
        {**raw, "settings": {"count": 6, "enabled": False}},
        {**raw, "settings": {"count": 2, "enabled": False, "extra": 1}},
        {**raw, "literal": {"pattern": "wrong", "nested": {"type": "literal"}}},
    ]
    for bad in bad_cases:
        with pytest.raises(Exception):
            validate_function_arguments(_spec(schema), bad)


def test_high_order_feature_witness_keeps_expected_request_boundary():
    """Configuration smoke check; populated interaction is covered by real Assist."""
    config = agent_config.normalize_agent_config(
        {
            "chat_model": "gpt-5.6",
            "api_mode": "responses",
            "reasoning_effort": "none",
            "memory_mode": "manual",
            "knowledge_enabled": True,
            "archive_enabled": True,
            "guest_mode_enabled": True,
            "function_groups": [
                {
                    "id": "nightly-on-demand",
                    "name": "Nightly on demand",
                    "description": "High-order witness",
                    "loading_mode": "on_demand",
                    "functions": [],
                    "enabled": True,
                }
            ],
            "functions": [],
        }
    )
    snapshot = build_provider_request_snapshot(config, {})
    assert snapshot.api_mode == "responses"
    assert snapshot.api_kwargs["model"] == "gpt-5.6"
    assert snapshot.api_kwargs["store"] is False


def _minimize_sequence(sequence, reproduces):
    """Simple deterministic ddmin-style reducer for persistent nightly repros."""
    current = list(sequence)
    granularity = 2
    while len(current) >= 2:
        chunk = max(1, len(current) // granularity)
        reduced = False
        for start in range(0, len(current), chunk):
            candidate = current[:start] + current[start + chunk :]
            if candidate and reproduces(candidate):
                current = candidate
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if not reduced:
            if granularity >= len(current):
                break
            granularity = min(len(current), granularity * 2)
    return current


def test_failure_sequence_reducer_keeps_same_failure_signature():
    sequence = [
        {"op": "setup"},
        {"op": "noise-a"},
        {"op": "write"},
        {"op": "cancel"},
        {"op": "noise-b"},
        {"op": "retry"},
        {"op": "noise-c"},
    ]

    expected_signature = "committed-write-cancel-retry"

    def observed_failure(candidate):
        initialized = committed = cancelled = False
        for item in candidate:
            operation = item["op"]
            if operation == "setup":
                initialized = True
            elif operation == "write" and initialized:
                committed = True
            elif operation == "cancel" and committed:
                cancelled = True
            elif operation == "retry" and committed and cancelled:
                return expected_signature
        return None

    def reproduces(candidate):
        return observed_failure(candidate) == expected_signature

    minimized = _minimize_sequence(sequence, reproduces)
    assert [item["op"] for item in minimized] == ["setup", "write", "cancel", "retry"]
    assert observed_failure(sequence) == expected_signature
    assert observed_failure(minimized) == expected_signature
    assert (
        observed_failure(
            [
                {"op": "setup"},
                {"op": "write"},
                {"op": "retry"},
                {"op": "cancel"},
            ]
        )
        is None
    )
    assert (
        observed_failure([item for item in minimized if item["op"] != "write"]) is None
    )
    assert (
        observed_failure([item for item in minimized if item["op"] != "setup"]) is None
    )


def test_saved_reproduction_envelope_is_seed_independent_and_serializable():
    sequence = [
        {"op": "write", "boundary": "after_replace"},
        {"op": "cancel", "target": "caller"},
        {"op": "retry", "storage": "healthy"},
    ]
    envelope = {
        "schema_version": 1,
        "source_sha": "0123456789abcdef",
        "environment": {"ha": "stable", "api": "responses"},
        "failure_signature": "write-cancel-retry-preserves-committed-generation",
        "operations": sequence,
    }
    encoded = json.dumps(envelope, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded == envelope
    assert "seed" not in decoded
    assert decoded["operations"] == sequence
