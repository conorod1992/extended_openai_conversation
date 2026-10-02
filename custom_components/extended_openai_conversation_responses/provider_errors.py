"""Safe provider failure classification and diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import re
from typing import Any

from openai import APIConnectionError, AuthenticationError, OpenAIError

_MAX_MESSAGE = 1000
_MAX_FIELD = 200
_LOGGER = logging.getLogger(__name__)
_OPENAI_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_LABELED_CREDENTIAL = re.compile(
    r"(?i)(\b(?:authorization|api[-_ ]?key)\b\s*[:=]\s*)(?:bearer\s+)?([^\s,;]+)"
)
_QUERY_CREDENTIAL = re.compile(r"(?i)([?&](?:api[-_]?key|access_token)=)([^&#\s]+)")
_BASIC_AUTH_URL = re.compile(r"(?i)(https?://)([^\s/@:]+):([^\s/@]+)@")


def _value(source: object | None, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _text(value: object | None, limit: int = _MAX_FIELD) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        return None
    return normalized[:limit]


def _safe_error_message(error: BaseException) -> str:
    """Return bounded provider text with known credential forms removed.

    Provider SDK errors are useful diagnostics, but some compatible providers echo
    request URLs or authentication headers in exception strings. Keep this deliberately
    targeted to credential shapes that can occur in provider errors instead of trying
    to classify arbitrary application text as a secret.
    """
    normalized = " ".join(str(error).split())
    if not normalized:
        return type(error).__name__
    normalized = _OPENAI_API_KEY.sub("[redacted]", normalized)
    normalized = _LABELED_CREDENTIAL.sub(r"\1[redacted]", normalized)
    normalized = _QUERY_CREDENTIAL.sub(r"\1[redacted]", normalized)
    normalized = _BASIC_AUTH_URL.sub(r"\1[redacted]@", normalized)
    return normalized[:_MAX_MESSAGE]


def _integer(value: object | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _root_cause_category(error: BaseException) -> tuple[str | None, str | None]:
    cause = error.__cause__ or error.__context__
    if cause is None:
        return None, None
    cause_type = type(cause).__name__
    lowered = cause_type.casefold()
    if "timeout" in lowered:
        category = "timeout"
    elif "ssl" in lowered or "tls" in lowered or "certificate" in lowered:
        category = "tls"
    elif "dns" in lowered or "name" in lowered:
        category = "dns"
    elif "connect" in lowered or "network" in lowered:
        category = "connection"
    else:
        category = "other"
    return cause_type[:_MAX_FIELD], category


class ProviderTransportError(OpenAIError):
    """Standard-library transport failure normalized into the provider path."""


def provider_transport_error(
    error: TimeoutError | ConnectionError,
) -> ProviderTransportError:
    """Normalize a raw timeout/connection failure without exposing its details."""
    if isinstance(error, TimeoutError):
        return ProviderTransportError("Provider request timed out")
    return ProviderTransportError("Could not connect to provider")


class ProviderStreamError(OpenAIError):
    """Structured provider failure reported inside an established response stream."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        error_type: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        response_id: str | None = None,
    ) -> None:
        super().__init__(_text(message, _MAX_MESSAGE) or "Provider stream failed")
        self.code = _text(code)
        self.type = _text(error_type)
        self.status_code = status_code
        self.request_id = _text(request_id)
        self.response_id = _text(response_id)


def provider_stream_error(
    prefix: str,
    source: object | None,
    *,
    response_id: object | None = None,
) -> ProviderStreamError:
    """Build an OpenAIError-compatible failure from a structured stream event."""
    message = _text(_value(source, "message"), _MAX_MESSAGE) or "unknown reason"
    status = _integer(_value(source, "status_code"))
    if status is None:
        status = _integer(_value(source, "status"))
    return ProviderStreamError(
        f"{prefix}: {message}",
        code=_text(_value(source, "code")),
        error_type=_text(_value(source, "type")),
        status_code=status,
        request_id=_text(_value(source, "request_id")),
        response_id=_text(response_id),
    )


def ensure_successful_responses_result(response: object) -> None:
    """Reject explicit failed/incomplete non-stream Responses API results."""
    response_id = _value(response, "id")
    error = _value(response, "error")
    if error is not None:
        raise provider_stream_error(
            "OpenAI response failed", error, response_id=response_id
        )

    status = _text(_value(response, "status"))
    if status == "incomplete":
        details = _value(response, "incomplete_details")
        reason = _text(_value(details, "reason")) or "unknown reason"
        raise ProviderStreamError(
            f"OpenAI response incomplete: {reason}",
            code=reason,
            error_type="incomplete",
            response_id=_text(response_id),
        )
    if status not in {None, "completed"}:
        raise ProviderStreamError(
            f"OpenAI response ended with status {status}",
            code=status,
            error_type="response_status",
            response_id=_text(response_id),
        )


def provider_error_metadata(error: BaseException) -> dict[str, Any]:
    """Return bounded failure metadata without request/response bodies or credentials."""
    metadata: dict[str, Any] = {
        "message": _safe_error_message(error),
    }
    status = _integer(getattr(error, "status_code", None))
    if status is not None:
        metadata["status_code"] = status
    for attribute, key in (
        ("code", "code"),
        ("type", "provider_error_type"),
        ("request_id", "provider_request_id"),
        ("response_id", "provider_response_id"),
    ):
        if value := _text(getattr(error, attribute, None)):
            metadata[key] = value
    cause_type, cause_category = _root_cause_category(error)
    if cause_type:
        metadata["root_cause_type"] = cause_type
        metadata["root_cause_category"] = cause_category
    return metadata


def provider_user_message(error: BaseException) -> str:
    """Return a concise provider error suitable for UI/Assist surfaces."""
    metadata = provider_error_metadata(error)
    message = str(metadata["message"])
    details: list[str] = []
    if status := metadata.get("status_code"):
        details.append(f"HTTP {status}")
    if code := metadata.get("code"):
        details.append(f"code {code}")
    if request_id := metadata.get("provider_request_id"):
        details.append(f"request {request_id}")
    result = f"{message} ({', '.join(details)})" if details else message
    if provider_failure_category(error) in {
        "model_unavailable",
        "insufficient_quota",
        "context_length",
        "unsupported_parameter",
    }:
        result += ". " + provider_log_remediation(error)
    return result


def provider_failure_category(error: BaseException) -> str:
    """Use explicit provider evidence, never a generic status, for specific causes."""
    code = str(getattr(error, "code", "") or "").casefold()
    error_type = str(getattr(error, "type", "") or "").casefold()
    # SDKs may retain the structured error inside body rather than attributes.
    body = getattr(error, "body", None)
    if isinstance(body, Mapping):
        source = body.get("error", body)
        if isinstance(source, Mapping):
            code = code or str(source.get("code", "") or "").casefold()
            error_type = error_type or str(source.get("type", "") or "").casefold()
    if code in {
        "model_not_found",
        "deploymentnotfound",
        "deployment_not_found",
        "model_not_available",
    }:
        return "model_unavailable"
    if (
        code
        in {"insufficient_quota", "billing_hard_limit_reached", "billing_limit_reached"}
        or error_type == "insufficient_quota"
    ):
        return "insufficient_quota"
    if code in {"context_length_exceeded", "max_context_length_exceeded"}:
        return "context_length"
    if code in {"unsupported_parameter", "unsupported_value"}:
        return "unsupported_parameter"
    return classify_config_provider_error(error)


def classify_config_provider_error(error: BaseException) -> str:
    """Map provider failures to stable Home Assistant config-flow categories."""
    status = _integer(getattr(error, "status_code", None))
    if isinstance(error, AuthenticationError) or status == 401:
        return "invalid_auth"
    if isinstance(error, (APIConnectionError, ProviderTransportError)):
        return "cannot_connect"
    if status == 403:
        return "provider_forbidden"
    if status == 429:
        return "provider_rate_limited"
    if status is not None and status >= 500:
        return "provider_unavailable"
    return "provider_error"


def request_reauthentication(hass: Any, entry: Any, error: BaseException) -> bool:
    """Start Home Assistant reauthentication for runtime authentication failures."""
    status = _integer(getattr(error, "status_code", None))
    if not isinstance(error, AuthenticationError) and status != 401:
        return False
    if entry is None:
        return False
    try:
        entry.async_start_reauth(hass)
    except Exception:
        _LOGGER.exception("Unable to start Extended OpenAI reauthentication flow")
        return False
    return True


def provider_log_remediation(error: BaseException) -> str:
    """Return concise user-actionable guidance for common provider failures."""
    category = provider_failure_category(error)
    if category == "model_unavailable":
        return (
            "Check the selected model or Azure deployment name and this provider "
            "account's access to it in the assistant configuration"
        )
    if category == "insufficient_quota":
        return "Check the provider account's billing balance, project quota and spending limit"
    if category == "context_length":
        return (
            "The provider's context limit was exceeded; shorten the conversation "
            "or reduce prompt/tool content, or select a model with a larger context window"
        )
    if category == "unsupported_parameter":
        return (
            "The provider rejected a parameter or value; review the selected model's "
            "API mode and advanced settings in the assistant configuration"
        )
    if category == "invalid_auth":
        return (
            "Check the API key or reauthenticate this Extended OpenAI provider "
            "in Settings > Devices & services"
        )
    if category == "cannot_connect":
        return (
            "Check the configured provider Base URL and Home Assistant network "
            "connectivity"
        )
    if category == "provider_forbidden":
        return (
            "Check the provider account/project permissions and access to the "
            "configured model"
        )
    if category == "provider_rate_limited":
        return "Check provider quota or rate limits and retry after the limit clears"
    if category == "provider_unavailable":
        return (
            "The provider is currently unavailable; check provider status and the "
            "configured endpoint"
        )
    return "Review the provider and model settings in Extended OpenAI"


def log_provider_failure(
    logger: logging.Logger, context: str, error: BaseException
) -> None:
    """Log safe diagnostics with remediation for user-fixable provider failures."""
    metadata = provider_error_metadata(error)
    # A provider may echo prompts, request bodies or arbitrary credentials in its
    # message. Deep provider text belongs in opt-in Request debugging, not HA logs.
    metadata.pop("message", None)
    metadata["error_type"] = type(error).__name__
    metadata["classification"] = provider_failure_category(error)
    logger.error(
        "%s. %s. Technical details: %s",
        context,
        provider_log_remediation(error),
        json.dumps(metadata, sort_keys=True),
    )
