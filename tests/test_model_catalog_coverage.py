"""Focused residual coverage for strict model-catalog validation."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import model_catalog as data


def _catalog() -> dict:
    value = deepcopy(data.BUNDLED_CATALOG)
    value["catalog_version"] += 1
    return value


def _model(value: dict) -> dict:
    return value["models"][0]


def _set_temperature(model: dict, support: str, allowed, send_policy: str) -> None:
    model["temperature"] = {
        "support": support,
        "allowed_reasoning_efforts": allowed,
        "send_policy": send_policy,
    }


@pytest.mark.parametrize(
    ("allowed", "match"),
    [
        ([], "Invalid conditional temperature reasoning efforts"),
        (["low", "low"], "Invalid conditional temperature reasoning efforts"),
        (["none"], "Invalid conditional temperature reasoning efforts"),
    ],
)
def test_conditional_sampling_requires_nonempty_unique_supported_efforts(
    allowed, match
) -> None:
    value = _catalog()
    _set_temperature(_model(value), "conditional", allowed, "omit_unless_configured")

    with pytest.raises(ValueError, match=match):
        data.validate_catalog(value)


def test_nonconditional_sampling_requires_null_allowed_efforts() -> None:
    value = _catalog()
    _set_temperature(_model(value), "always", ["low"], "omit_unless_configured")

    with pytest.raises(ValueError, match="allowed_reasoning_efforts must be null"):
        data.validate_catalog(value)


def test_unsupported_sampling_must_be_omitted() -> None:
    value = _catalog()
    _set_temperature(_model(value), "never", None, "omit_unless_configured")

    with pytest.raises(ValueError, match="must be omitted"):
        data.validate_catalog(value)


def test_auto_api_must_reference_an_enabled_api() -> None:
    value = _catalog()
    model = _model(value)
    model["api"]["responses"] = False

    with pytest.raises(ValueError, match="Invalid Auto API preference"):
        data.validate_catalog(value)


def test_function_calling_flags_must_be_real_booleans() -> None:
    value = _catalog()
    _model(value)["function_calling"]["responses"] = 1

    with pytest.raises(ValueError, match="Function-calling values must be boolean"):
        data.validate_catalog(value)


def test_function_preference_must_reference_enabled_api() -> None:
    value = _catalog()
    model = _model(value)
    model["auto_api"] = None
    model["api"]["responses"] = False
    model["function_calling"]["preferred_api"] = "responses"
    model["recommended_profile"]["api"] = "chat_completions"

    with pytest.raises(ValueError, match="Invalid preferred function-calling API"):
        data.validate_catalog(value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda reasoning: reasoning.update(supported="yes"),
        lambda reasoning: reasoning.update(efforts="low"),
        lambda reasoning: reasoning.update(efforts=["low", "low"]),
        lambda reasoning: reasoning.update(supported=False),
    ],
)
def test_reasoning_capability_shape_is_strict(mutate) -> None:
    value = _catalog()
    mutate(_model(value)["reasoning"])

    with pytest.raises(ValueError, match="Invalid reasoning"):
        data.validate_catalog(value)


def test_openai_reasoning_default_must_be_an_allowed_effort() -> None:
    value = _catalog()
    _model(value)["reasoning"]["openai_default"] = "none"

    with pytest.raises(ValueError, match="Invalid OpenAI reasoning default"):
        data.validate_catalog(value)


def test_streaming_flag_must_be_boolean() -> None:
    value = _catalog()
    _model(value)["streaming"] = 1

    with pytest.raises(ValueError, match="Streaming capability must be boolean"):
        data.validate_catalog(value)


def test_output_token_mapping_is_exact() -> None:
    value = _catalog()
    _model(value)["output_tokens"]["legacy_max_tokens"] = "max_tokens"

    with pytest.raises(ValueError, match="Invalid output-token parameter mapping"):
        data.validate_catalog(value)


def test_recommended_api_must_be_available() -> None:
    value = _catalog()
    model = _model(value)
    model["recommended_profile"]["api"] = "invalid"

    with pytest.raises(ValueError, match="Invalid recommended API"):
        data.validate_catalog(value)


def test_recommended_reasoning_effort_must_be_supported() -> None:
    value = _catalog()
    _model(value)["recommended_profile"]["reasoning_effort"] = "none"

    with pytest.raises(ValueError, match="Invalid recommended reasoning effort"):
        data.validate_catalog(value)


def test_recommended_sampling_profile_must_omit_sampling() -> None:
    value = _catalog()
    _model(value)["recommended_profile"]["temperature"] = 0.7

    with pytest.raises(ValueError, match="Recommended sampling profile must omit"):
        data.validate_catalog(value)


@pytest.mark.parametrize("field", ["service_tier", "explicit_prompt_cache"])
def test_compatibility_feature_flags_must_be_boolean(field: str) -> None:
    value = _catalog()
    _model(value)[field] = 1

    with pytest.raises(ValueError, match="Compatibility feature flags must be boolean"):
        data.validate_catalog(value)


def test_alias_target_and_lifecycle_note_are_strictly_validated() -> None:
    value = _catalog()
    _model(value)["alias_of"] = "INVALID MODEL ID"
    with pytest.raises(ValueError, match="Invalid alias target"):
        data.validate_catalog(value)

    value = _catalog()
    _model(value)["lifecycle_note"] = "x" * 513
    with pytest.raises(ValueError, match="Invalid lifecycle note"):
        data.validate_catalog(value)


def test_defaults_must_describe_unknown_models() -> None:
    value = _catalog()
    value["defaults"]["status"] = "current"

    with pytest.raises(ValueError, match="defaults must describe unknown models"):
        data.validate_catalog(value)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda model: model.update(display_name=""), "Invalid display name"),
        (lambda model: model.update(kind="family"), "Invalid model kind"),
    ],
)
def test_model_wrapper_metadata_is_strict(mutate, match) -> None:
    value = _catalog()
    mutate(_model(value))

    with pytest.raises(ValueError, match=match):
        data.validate_catalog(value)


def test_alias_must_reference_a_different_catalog_model() -> None:
    value = _catalog()
    model = _model(value)
    model["alias_of"] = model["id"]

    with pytest.raises(ValueError, match="Alias target must reference another"):
        data.validate_catalog(value)


def test_v1_migration_rejects_non_v1_and_preserves_monotonic_version() -> None:
    with pytest.raises(ValueError, match="Not a model catalogue v1 document"):
        data.migrate_catalog_v1({"schema_version": 2})

    migrated = data.migrate_catalog_v1(
        {"schema_version": 1, "catalog_version": data.BUNDLED_CATALOG["catalog_version"] + 5}
    )
    assert migrated["schema_version"] == 2
    assert migrated["catalog_version"] == data.BUNDLED_CATALOG["catalog_version"] + 6


def test_validate_or_migrate_marks_only_v1_as_migrated() -> None:
    migrated, changed = data.validate_or_migrate_catalog({"schema_version": 1})
    assert changed is True
    assert migrated["schema_version"] == 2

    current = _catalog()
    validated, changed = data.validate_or_migrate_catalog(current)
    assert changed is False
    assert validated == current
    assert validated is not current


def test_catalog_picker_preserves_selected_custom_model_case_insensitively() -> None:
    picked = data.catalog_picker_models(selected_model="MY-CUSTOM-MODEL")
    selected = next(item for item in picked if item["id"] == "my-custom-model")
    assert selected["status"] == "unknown"
    assert selected["display_name"] == "my-custom-model"


def test_sampling_rank_orders_supported_states() -> None:
    assert data._sampling_rank({"support": "always"}) == (3, frozenset())
    assert data._sampling_rank(
        {"support": "conditional", "allowed_reasoning_efforts": ["low", "high"]}
    ) == (2, frozenset({"low", "high"}))
    assert data._sampling_rank({"support": "undocumented"}) == (1, frozenset())
    assert data._sampling_rank({"support": "never"}) == (0, frozenset())


def test_transition_rejects_removed_reasoning_effort() -> None:
    candidate = _catalog()
    _model(candidate)["reasoning"]["efforts"].remove("max")

    with pytest.raises(ValueError, match="cannot remove reasoning effort choices"):
        data.validate_catalog_transition(None, candidate)


def test_transition_rejects_removed_api_path() -> None:
    candidate = _catalog()
    model = _model(candidate)
    model["api"]["responses"] = False
    model["auto_api"] = "chat_completions"
    model["function_calling"].update(
        responses=False, chat_completions=True, preferred_api="chat_completions"
    )
    model["recommended_profile"]["api"] = "chat_completions"

    with pytest.raises(ValueError, match="cannot remove an API path"):
        data.validate_catalog_transition(None, candidate)


def test_transition_rejects_removed_function_calling_support() -> None:
    candidate = _catalog()
    model = _model(candidate)
    model["function_calling"]["responses"] = False
    model["function_calling"]["preferred_api"] = "chat_completions"

    with pytest.raises(ValueError, match="cannot remove function-calling support"):
        data.validate_catalog_transition(None, candidate)


def test_transition_rejects_lower_max_output_limit() -> None:
    candidate = _catalog()
    _model(candidate)["limits"]["max_output_tokens"] -= 1

    with pytest.raises(ValueError, match="cannot lower max output"):
        data.validate_catalog_transition(None, candidate)


def test_transition_rejects_sampling_support_downgrade() -> None:
    current = _catalog()
    candidate = _catalog()
    _set_temperature(_model(current), "always", None, "omit_unless_configured")
    _set_temperature(
        _model(candidate), "conditional", ["low"], "omit_unless_configured"
    )

    with pytest.raises(ValueError, match="cannot narrow temperature"):
        data.validate_catalog_transition(current, candidate)


def test_transition_rejects_narrower_conditional_sampling_efforts() -> None:
    current = _catalog()
    candidate = _catalog()
    _set_temperature(
        _model(current), "conditional", ["low", "medium"], "omit_unless_configured"
    )
    _set_temperature(
        _model(candidate), "conditional", ["low"], "omit_unless_configured"
    )

    with pytest.raises(ValueError, match="cannot narrow temperature"):
        data.validate_catalog_transition(current, candidate)


def test_activate_catalog_can_restore_bundled_state() -> None:
    custom = _catalog()
    _model(custom)["display_name"] = "Updated Astra"
    data.activate_catalog(custom)
    assert data.model_metadata("gpt-6-astra")["display_name"] == "Updated Astra"

    data.activate_catalog(None)
    assert data.model_metadata("gpt-6-astra")["display_name"] == "gpt-6-astra"
