"""AI Task model-option presentation tests."""

from custom_components.extended_openai_conversation_responses.config_flow import (
    _ai_task_display_reasoning_effort,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
)
from custom_components.extended_openai_conversation_responses.model_capabilities import (
    parameter_is_allowed,
)


def test_reconfigure_same_model_uses_saved_effort_for_sampling_visibility() -> None:
    """A saved non-default effort must drive conditional sampling controls."""
    options = {
        CONF_CHAT_MODEL: "gpt-5.4",
        CONF_REASONING_EFFORT: "high",
    }

    effort = _ai_task_display_reasoning_effort(
        "gpt-5.4", options, reconfigure=True
    )

    assert effort == "high"
    assert parameter_is_allowed("gpt-5.4", "temperature", effort) is False
    assert parameter_is_allowed("gpt-5.4", "top_p", effort) is False


def test_reconfigure_same_model_saved_none_keeps_sampling_visible() -> None:
    """A saved effort that permits sampling must continue to expose the controls."""
    options = {
        CONF_CHAT_MODEL: "gpt-5.4",
        CONF_REASONING_EFFORT: "none",
    }

    effort = _ai_task_display_reasoning_effort(
        "gpt-5.4", options, reconfigure=True
    )

    assert effort == "none"
    assert parameter_is_allowed("gpt-5.4", "temperature", effort) is True
    assert parameter_is_allowed("gpt-5.4", "top_p", effort) is True


def test_new_task_uses_catalog_recommendation_not_default_options() -> None:
    """Unsaved defaults must not override the selected model recommendation."""
    options = {
        CONF_CHAT_MODEL: "gpt-5.4",
        CONF_REASONING_EFFORT: "high",
    }

    assert (
        _ai_task_display_reasoning_effort(
            "gpt-5.4", options, reconfigure=False
        )
        == "none"
    )


def test_changed_or_stale_model_effort_falls_back_to_recommendation() -> None:
    """Saved effort applies only to the same model and only while still valid."""
    previous_model = {
        CONF_CHAT_MODEL: "gpt-5.4",
        CONF_REASONING_EFFORT: "high",
    }
    stale_effort = {
        CONF_CHAT_MODEL: "gpt-5.4",
        CONF_REASONING_EFFORT: "max",
    }

    assert (
        _ai_task_display_reasoning_effort(
            "gpt-5.2", previous_model, reconfigure=True
        )
        == "none"
    )
    assert (
        _ai_task_display_reasoning_effort(
            "gpt-5.4", stale_effort, reconfigure=True
        )
        == "none"
    )
