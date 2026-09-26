"""Central model-capability validation and request normalization."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, cast

from .const import API_MODE_AUTO, API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES
from .model_catalog import (
    compatibility_capabilities,
    evaluate_tool_rule,
    model_metadata,
)


class ModelCapabilityError(ValueError):
    """A configuration is incompatible with the selected model capabilities."""


_ACTIVE_CAPABILITY_SNAPSHOT: ContextVar[tuple[str, Mapping[str, Any]] | None] = (
    ContextVar("extended_openai_model_capability_snapshot", default=None)
)


def _request_capabilities(model_id: str) -> Mapping[str, Any]:
    """Return the request-local snapshot when one is active for this model."""
    snapshot = _ACTIVE_CAPABILITY_SNAPSHOT.get()
    if snapshot is not None and snapshot[0] == model_id:
        return snapshot[1]
    return get_model_capabilities(model_id)


@contextmanager
def model_capability_snapshot(
    model_id: str, capabilities: Mapping[str, Any]
) -> Iterator[None]:
    """Reuse one already-isolated capability snapshot during request preparation."""
    token = _ACTIVE_CAPABILITY_SNAPSHOT.set((model_id, capabilities))
    try:
        yield
    finally:
        _ACTIVE_CAPABILITY_SNAPSHOT.reset(token)


def get_model_capabilities(model_id: str) -> dict[str, Any]:
    """Return authoritative v2 capability data for one exact model ID."""
    return model_metadata(model_id)


def frontend_capabilities(
    model: str, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Send evaluated choices to the UI without browser-side provider rules."""
    metadata = metadata or model_metadata(model)
    result = compatibility_capabilities(model, metadata=metadata)
    with model_capability_snapshot(model, metadata):
        result["auto_paths"] = {}
        for effort in (None, *metadata["reasoning"]["efforts"]):
            for functions in (False, True):
                for web_search in (False, True):
                    key = f"{effort}:{int(functions)}:{int(web_search)}"
                    try:
                        result["auto_paths"][key] = select_api_path(
                            model, API_MODE_AUTO, functions, effort, web_search
                        )
                    except ModelCapabilityError:
                        result["auto_paths"][key] = None
    return result


def reasoning_efforts_for_api(model: str, api: str) -> list[str]:
    return list(_request_capabilities(model)["reasoning"]["by_api"][api]["efforts"])


def capability_allowed(
    model: str,
    tool: str,
    api: str,
    *,
    effort: str | None = None,
    service_tier: str | None = None,
    streaming: bool | None = None,
) -> bool:
    """Evaluate the catalog's bounded, explicit condition dimensions."""
    capabilities = _request_capabilities(model)
    if not capabilities["api"].get(api):
        return False
    if "tools" not in capabilities:
        if tool == "web_search":
            return api == API_MODE_RESPONSES and bool(
                capabilities.get("responses_web_search")
            )
        legacy = capabilities["function_calling"][api]
        return (
            legacy
            if type(legacy) is bool
            else effort in legacy["allowed_reasoning_efforts"]
        )
    return evaluate_tool_rule(
        capabilities["tools"][tool][api],
        effort=effort,
        service_tier=service_tier,
        streaming=streaming,
    )


def validate_reasoning_effort(
    model: str, effort: str | None, api: str | None = None
) -> str | None:
    """Validate an exact model reasoning enum without family-name heuristics."""
    reasoning = _request_capabilities(model)["reasoning"]
    if not reasoning["supported"]:
        if effort is not None:
            raise ModelCapabilityError(
                f"{model} does not support reasoning_effort; omit the setting."
            )
        return None
    if effort is None:
        return None
    choices = (
        reasoning["efforts"] if api is None else reasoning["by_api"][api]["efforts"]
    )
    if effort not in choices:
        allowed = ", ".join(choices)
        raise ModelCapabilityError(
            f"Invalid reasoning_effort {effort!r} for {model}{' through ' + api if api else ''}; allowed: {allowed}."
        )
    return effort


def parameter_is_allowed(model: str, parameter: str, effort: str | None) -> bool:
    """Return whether temperature/top_p may be sent for this exact request."""
    if parameter not in {"temperature", "top_p"}:
        raise ModelCapabilityError(f"Unknown sampling parameter: {parameter}")
    capability = _request_capabilities(model)[parameter]
    support = capability["support"]
    if support == "always":
        return True
    if support == "conditional":
        return effort in (capability["allowed_reasoning_efforts"] or [])
    return False


def validate_api_path(
    model: str,
    api: str,
    tools_required: bool = False,
    effort: str | None = None,
    web_search: bool = False,
) -> str:
    """Validate API and function-calling support for one selected path."""
    if api not in {API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS}:
        raise ModelCapabilityError(f"Unknown API path: {api}")
    capabilities = _request_capabilities(model)
    if not capabilities["api"][api]:
        raise ModelCapabilityError(f"{model} does not support {api}.")
    validate_reasoning_effort(model, effort, api)
    if tools_required and not capability_allowed(model, "function", api, effort=effort):
        raise ModelCapabilityError(
            f"{model} does not support function/tool calling through {api} at reasoning_effort={effort}."
        )
    if web_search and not capability_allowed(model, "web_search", api, effort=effort):
        raise ModelCapabilityError(
            f"{model} does not support Web Search through {api} at reasoning_effort={effort}."
        )
    return api


def select_api_path(
    model: str,
    configured_api: str,
    tools_required: bool = False,
    effort: str | None = None,
    web_search: bool = False,
) -> str:
    """Resolve Auto entirely from exact model capability metadata."""
    capabilities = _request_capabilities(model)
    if configured_api != API_MODE_AUTO:
        return validate_api_path(
            model, configured_api, tools_required, effort, web_search
        )

    def compatible(api: str) -> bool:
        try:
            validate_api_path(model, api, tools_required, effort, web_search)
            return True
        except ModelCapabilityError:
            return False

    if tools_required:
        preferred = cast(str, capabilities["function_calling"]["preferred_api"])
        if compatible(preferred):
            return preferred
        for api in (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS):
            if compatible(api):
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
        preferred = (
            cast(str | None, capabilities.get("auto_api")) or API_MODE_CHAT_COMPLETIONS
        )
    if compatible(preferred):
        return preferred
    for api in (API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES):
        if compatible(api):
            return api
    raise ModelCapabilityError(f"{model} has no supported conversational API path.")


def recommended_reasoning_effort(model: str) -> str | None:
    """Return the HA/application default, separately from the provider default."""
    capabilities = _request_capabilities(model)
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
    capabilities = _request_capabilities(model)
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
