"""Additional strict Model Catalog coverage."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import model_catalog


def _model(model_id: str = "gpt-6-astra") -> dict:
    return deepcopy(
        next(
            item
            for item in model_catalog.BUNDLED_CATALOG["models"]
            if item["id"] == model_id
        )
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda m: m.__setitem__(
                "output_tokens",
                {
                    "responses": "wrong",
                    "chat_completions": "max_completion_tokens",
                    "legacy_max_tokens": "never_send",
                },
            ),
            "output-token",
        ),
        (
            lambda m: m["recommended_profile"].__setitem__("api", "invalid"),
            "recommended API",
        ),
        (
            lambda m: m["recommended_profile"].__setitem__(
                "reasoning_effort", "impossible"
            ),
            "recommended reasoning effort",
        ),
        (
            lambda m: m["recommended_profile"].__setitem__("temperature", "send"),
            "sampling profile",
        ),
        (
            lambda m: m.__setitem__("service_tiers", ["auto", "auto"]),
            "service-tier",
        ),
        (
            lambda m: m.__setitem__("service_tiers", ["auto", "made-up"]),
            "service-tier",
        ),
        (
            lambda m: m.__setitem__("explicit_prompt_cache", "yes"),
            "service-tier",
        ),
        (
            lambda m: m.__setitem__("structured_outputs", 1),
            "service-tier",
        ),
        (
            lambda m: m.__setitem__("responses_web_search", None),
            "service-tier",
        ),
        (
            lambda m: m.__setitem__("alias_of", "not a valid model id"),
            "alias target",
        ),
        (
            lambda m: m.__setitem__("lifecycle_note", "x" * 513),
            "lifecycle note",
        ),
        (
            lambda m: m.__setitem__("deprecated_at", 20261001),
            "deprecated_at date",
        ),
        (
            lambda m: m.__setitem__("deprecated_at", "not-a-date"),
            "deprecated_at date",
        ),
    ],
)
def test_model_metadata_rejects_remaining_invalid_contracts(mutation, message) -> None:
    model = _model()
    mutation(model)

    with pytest.raises(ValueError, match=message):
        model_catalog._validate_metadata(model, model_entry=True)


def test_model_metadata_rejects_recommended_effort_not_supported_by_selected_api() -> None:
    model = _model("gpt-6-sol")
    assert "max" in model["reasoning"]["efforts"]
    assert "max" not in model["reasoning"]["by_api"]["chat_completions"]["efforts"]
    model["recommended_profile"]["api"] = "chat_completions"
    model["recommended_profile"]["reasoning_effort"] = "max"

    with pytest.raises(
        ValueError,
        match="recommended reasoning effort for selected API",
    ):
        model_catalog._validate_metadata(model, model_entry=True)


def test_model_metadata_requires_deprecated_status_for_lifecycle_dates() -> None:
    model = _model()
    model["deprecated_at"] = "2026-10-01"

    with pytest.raises(ValueError, match="Lifecycle dates require deprecated"):
        model_catalog._validate_metadata(model, model_entry=True)


def test_model_metadata_rejects_shutdown_before_deprecation() -> None:
    model = _model()
    model["status"] = "deprecated"
    model["deprecated_at"] = "2027-04-01"
    model["shutdown_at"] = "2026-10-01"

    with pytest.raises(ValueError, match="shutdown date cannot precede"):
        model_catalog._validate_metadata(model, model_entry=True)


@pytest.mark.parametrize(
    ("support", "expected_rank", "expected_allowed"),
    [
        ({"support": "always", "allowed_reasoning_efforts": None}, 3, frozenset()),
        (
            {
                "support": "conditional",
                "allowed_reasoning_efforts": ["low", "high"],
            },
            2,
            frozenset({"low", "high"}),
        ),
        (
            {"support": "undocumented", "allowed_reasoning_efforts": None},
            1,
            frozenset(),
        ),
        ({"support": "never", "allowed_reasoning_efforts": None}, 0, frozenset()),
    ],
)
def test_sampling_rank_covers_all_support_classes(
    support, expected_rank, expected_allowed
) -> None:
    assert model_catalog._sampling_rank(support) == (
        expected_rank,
        expected_allowed,
    )


@pytest.mark.parametrize(
    ("support", "effort", "expected"),
    [
        (True, None, True),
        (False, "low", False),
        (
            {
                "support": "conditional",
                "allowed_reasoning_efforts": ["low"],
            },
            "low",
            True,
        ),
        (
            {
                "support": "conditional",
                "allowed_reasoning_efforts": ["low"],
            },
            "high",
            False,
        ),
    ],
)
def test_function_calling_allowed_support_shapes(support, effort, expected) -> None:
    assert model_catalog.function_calling_allowed(support, effort) is expected


def test_tool_rule_evaluation_honours_requires_and_excludes() -> None:
    rule = {
        "support": "conditional",
        "requires": {"reasoning_effort": ["low"], "service_tier": ["priority"]},
        "excludes": {"streaming": [False]},
    }

    assert model_catalog.evaluate_tool_rule(
        rule,
        effort="low",
        service_tier="priority",
        streaming=True,
    )
    assert not model_catalog.evaluate_tool_rule(
        rule,
        effort="high",
        service_tier="priority",
        streaming=True,
    )
    assert not model_catalog.evaluate_tool_rule(
        rule,
        effort="low",
        service_tier="priority",
        streaming=False,
    )
    assert not model_catalog.evaluate_tool_rule(
        {"support": "never"},
        effort="low",
    )


@pytest.mark.parametrize(
    ("support", "expected"),
    [
        (True, None),
        (False, frozenset()),
        (
            {
                "support": "conditional",
                "allowed_reasoning_efforts": ["low"],
            },
            frozenset({"low"}),
        ),
    ],
)
def test_function_support_set_shapes(support, expected) -> None:
    assert model_catalog._function_support_set(support) == expected
