"""Safe provider failure classification and diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from typing import Any

from openai import APIConnectionError, AuthenticationError, OpenAIError

_MAX_MESSAGE = 1000
_MAX_FIELD = 200
_LOGGER = logging.getLogger(__name__)


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
        "message": _text(str(error), _MAX_MESSAGE) or type(error).__name__,
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
    return f"{message} ({', '.join(details)})" if details else message


def classify_config_provider_error(error: BaseException) -> str:
    """Map provider failures to stable Home Assistant config-flow categories."""
    status = _integer(getattr(error, "status_code", None))
    if isinstance(error, AuthenticationError) or status == 401:
        return "invalid_auth"
    if isinstance(error, APIConnectionError):
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


def log_provider_failure(
    logger: logging.Logger, context: str, error: BaseException
) -> None:
    """Log only bounded, explicitly safe provider failure fields."""
    logger.error(
        "%s: %s",
        context,
        json.dumps(provider_error_metadata(error), sort_keys=True),
    )
