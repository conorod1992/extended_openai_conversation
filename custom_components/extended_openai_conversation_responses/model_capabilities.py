"""Central model-capability validation and request normalization."""

from __future__ import annotations

from typing import Any, cast

from .const import API_MODE_AUTO, API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES
from .model_catalog import model_metadata


class ModelCapabilityError(ValueError):
    """A configuration is incompatible with the selected model capabilities."""


def get_model_capabilities(model_id: str) -> dict[str, Any]:
    """Return authoritative v2 capability data for one exact model ID."""
    return model_metadata(model_id)


def validate_reasoning_effort(model: str, effort: str | None) -> str | None:
    """Validate an exact model reasoning enum without family-name heuristics."""
    reasoning = get_model_capabilities(model)["reasoning"]
    if not reasoning["supported"]:
        if effort is not None:
            raise ModelCapabilityError(
                f"{model} does not support reasoning_effort; omit the setting."
            )
        return None
    if effort is None:
        return None
    if effort not in reasoning["efforts"]:
        allowed = ", ".join(reasoning["efforts"])
        raise ModelCapabilityError(
            f"Invalid reasoning_effort {effort!r} for {model}; allowed: {allowed}."
        )
    return effort


def parameter_is_allowed(model: str, parameter: str, effort: str | None) -> bool:
    """Return whether temperature/top_p may be sent for this exact request."""
    if parameter not in {"temperature", "top_p"}:
        raise ModelCapabilityError(f"Unknown sampling parameter: {parameter}")
    capability = get_model_capabilities(model)[parameter]
    support = capability["support"]
    if support == "always":
        return True
    if support == "conditional":
        return effort in (capability["allowed_reasoning_efforts"] or [])
    return False


def validate_api_path(model: str, api: str, tools_required: bool = False) -> str:
    """Validate API and function-calling support for one selected path."""
    if api not in {API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS}:
        raise ModelCapabilityError(f"Unknown API path: {api}")
    capabilities = get_model_capabilities(model)
    if not capabilities["api"][api]:
        raise ModelCapabilityError(f"{model} does not support {api}.")
    if tools_required and not capabilities["function_calling"][api]:
        raise ModelCapabilityError(
            f"{model} does not support function/tool calling through {api}."
        )
    return api


def select_api_path(
    model: str, configured_api: str, tools_required: bool = False
) -> str:
    """Resolve Auto entirely from exact model capability metadata."""
    capabilities = get_model_capabilities(model)
    if configured_api != API_MODE_AUTO:
        return validate_api_path(model, configured_api, tools_required)

    if tools_required:
        preferred = cast(str, capabilities["function_calling"]["preferred_api"])
        if capabilities["api"].get(preferred) and capabilities["function_calling"].get(
            preferred
        ):
            return preferred
        for api in (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS):
            if capabilities["api"][api] and capabilities["function_calling"][api]:
                return api
        raise ModelCapabilityError(
            f"{model} has no supported API path for function/tool calling."
        )

    # Unknown/custom models remain conservative and use Responses. Known models
    # may opt into a different no-tool Auto path through explicit catalog data;
    # otherwise preserve the longstanding Chat-first fallback.
    if capabilities["status"] == "unknown":
        preferred = API_MODE_RESPONSES
    else:
        preferred = cast(
            str | None, capabilities.get("auto_api")
        ) or API_MODE_CHAT_COMPLETIONS
    if capabilities["api"].get(preferred):
        return preferred
    for api in (API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES):
        if capabilities["api"][api]:
            return api
    raise ModelCapabilityError(f"{model} has no supported conversational API path.")


def recommended_reasoning_effort(model: str) -> str | None:
    """Return the HA/application default, separately from the provider default."""
    capabilities = get_model_capabilities(model)
    return cast(str | None, capabilities["recommended_profile"]["reasoning_effort"])


def normalize_output_token_limit(
    model: str, api: str, configured_limit: int | str | None
) -> tuple[str, int] | None:
    """Validate the model ceiling and return the modern path-specific field/value."""
    validate_api_path(model, api, False)
    if configured_limit is None or configured_limit == "":
        return None
    try:
        value = int(configured_limit)
    except (TypeError, ValueError) as err:
        raise ModelCapabilityError("Output token limit must be an integer.") from err
    if value <= 0:
        raise ModelCapabilityError("Output token limit must be greater than zero.")
    capabilities = get_model_capabilities(model)
    ceiling = capabilities["limits"]["max_output_tokens"]
    if value > ceiling:
        raise ModelCapabilityError(
            f"Output token limit {value} exceeds {model}'s maximum of {ceiling}."
        )
    field = cast(str, capabilities["output_tokens"][api])
    if field == "max_tokens":
        raise ModelCapabilityError("Legacy max_tokens must never be emitted.")
    return field, value


def sampling_value_is_configured(
    parameter: str, value: Any, legacy_default: Any
) -> bool:
    """Treat legacy default-valued sampling options as omitted after v1 migration."""
    if parameter not in {"temperature", "top_p"}:
        raise ModelCapabilityError(f"Unknown sampling parameter: {parameter}")
    return value is not None and value != legacy_default
