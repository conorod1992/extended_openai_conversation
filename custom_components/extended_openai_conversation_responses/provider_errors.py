"""Safe provider failure classification and diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
import errno
import hashlib
import json
import logging
import re
import socket
import ssl
from typing import Any

import httpx
from openai import APIConnectionError, AuthenticationError, OpenAIError

from homeassistant.const import CONF_API_KEY

from .const import CONF_API_PROVIDER, CONF_API_VERSION, CONF_BASE_URL, CONF_ORGANIZATION
from .operational_errors import failure_causes

_MAX_MESSAGE = 1000
_MAX_FIELD = 200
_LOGGER = logging.getLogger(__name__)
_AUTH_FAILURES = f"{__package__}.provider_auth_failures"
_PARAMETER_SETTINGS = {
    "temperature": "Response creativity",
    "top_p": "Response diversity",
    "max_tokens": "Maximum response length",
    "max_completion_tokens": "Maximum response length",
    "max_output_tokens": "Maximum response length",
    "reasoning_effort": "Reasoning effort",
    "reasoning.effort": "Reasoning effort",
    "service_tier": "Processing tier",
    "tools": "Function Tools and Web Search",
    "response_format": "Structured Outputs",
    "text.format": "Structured Outputs",
}


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
    """Return a fixed category message, never remote prose or opaque identifiers."""
    return {
        "invalid_auth": "Provider authentication failed",
        "cannot_connect": "Could not connect to provider",
        "provider_forbidden": "Provider access was refused",
        "provider_rate_limited": "Provider rate limit reached",
        "provider_unavailable": "Provider is temporarily unavailable",
        "model_unavailable": "The configured model is unavailable",
        "insufficient_quota": "Provider quota or billing limit reached",
        "context_length": "Provider context limit exceeded",
        "unsupported_parameter": "Provider rejected an unsupported parameter",
    }.get(provider_failure_category(error), "Provider request failed")


def _integer(value: object | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _root_cause_category(error: BaseException) -> tuple[str | None, str | None]:
    findings = []
    for cause in failure_causes(error):
        name = type(cause).__name__[:_MAX_FIELD]
        if isinstance(cause, socket.gaierror):
            category = "dns"
        elif isinstance(cause, ssl.SSLError):
            category = "tls"
        elif isinstance(cause, ConnectionRefusedError) or (
            isinstance(cause, OSError) and cause.errno == errno.ECONNREFUSED
        ):
            category = "connection_refused"
        elif isinstance(cause, httpx.ReadTimeout):
            category = "read_timeout"
        elif isinstance(cause, httpx.ConnectTimeout):
            category = "connect_timeout"
        elif isinstance(cause, (httpx.ReadError, httpx.RemoteProtocolError)):
            category = "interrupted_stream"
        elif isinstance(cause, (TimeoutError, httpx.TimeoutException)):
            category = "timeout"
        elif isinstance(cause, (ConnectionError, httpx.ConnectError)):
            category = "connection"
        elif cause is not error:
            category = {
                "ReadTimeout": "timeout",
                "SSLError": "tls",
                "DNSLookupError": "dns",
                "NetworkError": "connection",
            }.get(name, "other")
        else:
            continue
        findings.append((name, category))
    for finding in reversed(findings):
        if finding[1] not in {"other", "timeout", "connection"}:
            return finding
    return next(
        (item for item in reversed(findings) if item[1] != "other"),
        findings[-1] if findings else (None, None),
    )


def _rejected_parameter_setting(error: BaseException) -> str | None:
    body = getattr(error, "body", None)
    source = body.get("error", body) if isinstance(body, Mapping) else None
    param = getattr(error, "param", None) or _value(source, "param")
    return _PARAMETER_SETTINGS.get(param) if isinstance(param, str) else None


class ProviderTransportError(OpenAIError):
    """Raw transport failure normalized into the provider path."""


def provider_transport_error(
    error: TimeoutError | ConnectionError | httpx.RequestError,
) -> ProviderTransportError:
    """Normalize a raw timeout/connection failure without exposing its details."""
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
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


def responses_incomplete_reason(response: object | None) -> str | None:
    """Read the reason supplied by a Responses terminal or completed object."""
    return _text(_value(_value(response, "incomplete_details"), "reason"))


def ensure_successful_responses_result(response: object) -> None:
    """Reject explicit failed or incomplete Responses API results."""
    response_id = _value(response, "id")
    error = _value(response, "error")
    if error is not None:
        raise provider_stream_error(
            "OpenAI response failed", error, response_id=response_id
        )

    raw_status = _value(response, "status")
    status = _text(raw_status)
    if raw_status is not None and status is None:
        raise ProviderStreamError(
            "OpenAI response status is malformed",
            error_type="response_status",
            response_id=_text(response_id),
        )
    if status == "incomplete":
        reason = responses_incomplete_reason(response) or "unknown reason"
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
    if status is not None and 100 <= status <= 599:
        metadata["status_code"] = status
    metadata["code"] = provider_failure_category(error)
    cause_type, cause_category = _root_cause_category(error)
    if cause_type:
        metadata["root_cause_type"] = cause_type
        metadata["root_cause_category"] = cause_category
    if provider_failure_category(error) == "unsupported_parameter" and (
        setting := _rejected_parameter_setting(error)
    ):
        metadata["affected_setting"] = setting
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
        "invalid_schema",
    } or metadata.get("status_code") in {400, 404}:
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
    if code in {"invalid_json_schema", "invalid_response_format_schema"}:
        return "invalid_schema"
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


def provider_authentication_snapshot(entry: Any) -> tuple[str, str]:
    """Identify a client's authentication configuration without retaining secrets."""
    data = getattr(entry, "data", {})
    authentication = [
        data.get(CONF_API_KEY),
        data.get(CONF_BASE_URL),
        data.get(CONF_API_VERSION),
        data.get(CONF_ORGANIZATION),
        data.get(CONF_API_PROVIDER),
    ]
    fingerprint = hashlib.sha256(json.dumps(authentication).encode()).hexdigest()
    return str(getattr(entry, "entry_id", "")), fingerprint


def request_reauthentication(hass: Any, entry: Any, error: BaseException) -> bool:
    """Start Home Assistant reauthentication for runtime authentication failures."""
    status = _integer(getattr(error, "status_code", None))
    if not isinstance(error, AuthenticationError) and status != 401:
        return False
    if entry is None:
        return False
    originating_auth = getattr(error, "_eoai_authentication_snapshot", None)
    current_auth = provider_authentication_snapshot(entry)
    if originating_auth is not None and originating_auth != current_auth:
        return False
    if isinstance(getattr(hass, "data", None), dict) and getattr(
        entry, "entry_id", None
    ):
        hass.data.setdefault(_AUTH_FAILURES, {})[entry.entry_id] = current_auth
    try:
        entry.async_start_reauth(hass)
    except Exception:
        _LOGGER.exception("Unable to start Extended OpenAI reauthentication flow")
        return False
    return True


def provider_authentication_rejected(hass: Any, entry: Any) -> bool:
    """Return whether this connection's current credentials were rejected."""
    data = getattr(hass, "data", None)
    if not isinstance(data, dict):
        return False
    failure = data.get(_AUTH_FAILURES, {}).get(getattr(entry, "entry_id", None))
    return failure is not None and failure == provider_authentication_snapshot(entry)


def clear_provider_authentication_rejection(
    hass: Any, entry: Any, authentication: tuple[str, str] | None = None
) -> None:
    """Clear only the observation belonging to a successful connection or removed entry."""
    data = getattr(hass, "data", None)
    if not isinstance(data, dict):
        return
    failures = data.get(_AUTH_FAILURES, {})
    entry_id = getattr(entry, "entry_id", None)
    if entry_id in failures and (
        authentication is None or failures[entry_id] == authentication
    ):
        failures.pop(entry_id)


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
        if setting := _rejected_parameter_setting(error):
            return f"Review {setting} and the selected model's supported settings"
        return (
            "The provider rejected a parameter or value; review the selected model's "
            "API mode and advanced settings in the assistant configuration"
        )
    if category == "invalid_schema":
        return "The provider rejected the requested output schema. Check the structure and the model's supported schema features"
    if category == "invalid_auth":
        return (
            "Check the API key or reauthenticate this Extended OpenAI provider "
            "in Settings > Devices & services"
        )
    if category == "cannot_connect":
        _, transport = _root_cause_category(error)
        guidance = {
            "dns": "Check the provider hostname in Base URL and Home Assistant DNS",
            "tls": "Check the provider certificate, Base URL and Home Assistant TLS trust",
            "connection_refused": "Check that the provider is listening on the host and port in Base URL",
            "connect_timeout": "The connection timed out; check the Base URL and network reachability",
            "read_timeout": "The provider stopped responding; check service health and proxy timeouts",
            "interrupted_stream": "The connection closed during the reply; check provider and proxy streaming support",
        }
        if transport in guidance:
            return guidance[transport]
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
    if _integer(getattr(error, "status_code", None)) in {400, 404}:
        return (
            "Check provider and model settings, including Base URL and its API path, the selected model or deployment, "
            "and whether the provider supports the selected API format and parameters"
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
    if request := getattr(error, "_eoai_provider_request", None):
        metadata["effective_request"] = request
    logger.error(
        "%s. %s. Technical details: %s",
        context,
        provider_log_remediation(error),
        json.dumps(metadata, sort_keys=True),
    )


def annotate_provider_request(
    error: BaseException,
    model: str,
    api_mode: str,
    stage: str,
    run_id: str | None,
    *,
    routing_rule: str | None = None,
    retained_routing_rules: tuple[str, ...] = (),
) -> None:
    """Attach identifiers from the actual failing provider round."""
    safe_model = (
        model
        if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", model)
        and not model.startswith(("sk-", "sess-"))
        else "<custom model>"
    )
    request: dict[str, str | list[str]] = {
        "model": safe_model,
        "api_mode": api_mode,
        "stage": stage,
    }
    if isinstance(run_id, str) and run_id:
        request["operation"] = hashlib.sha256(
            run_id.encode(errors="surrogatepass")
        ).hexdigest()[:12]
    if isinstance(routing_rule, str) and re.fullmatch(
        r"[A-Za-z0-9_-]{1,80}", routing_rule
    ):
        request["routing_rule"] = routing_rule
    retained = [
        rule
        for rule in retained_routing_rules
        if rule != routing_rule and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rule)
    ]
    if retained:
        request["retained_routing_rules"] = retained
    error.__dict__["_eoai_provider_request"] = request
