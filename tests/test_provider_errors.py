"""Tests for provider error normalization and diagnostics."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
    ProviderTransportError,
    classify_config_provider_error,
    ensure_successful_responses_result,
    log_provider_failure,
    provider_error_metadata,
    provider_stream_error,
    provider_transport_error,
    provider_user_message,
    request_reauthentication,
)


def test_provider_stream_error_reads_object_fields_and_status_fallback() -> None:
    """Structured object fields are preserved and ``status`` is a valid fallback."""
    source = SimpleNamespace(
        message="  quota   exhausted  ",
        code="rate_limit_exceeded",
        type="rate_limit_error",
        status=429,
        request_id="req-123",
    )

    error = provider_stream_error(
        "OpenAI response failed", source, response_id="resp-456"
    )

    assert isinstance(error, ProviderStreamError)
    assert str(error) == "OpenAI response failed: quota exhausted"
    assert error.status_code == 429
    assert error.code == "rate_limit_exceeded"
    assert error.type == "rate_limit_error"
    assert error.request_id == "req-123"
    assert error.response_id == "resp-456"


def test_provider_stream_error_missing_optional_fields_uses_only_message_fallback() -> None:
    """Missing optional provider fields do not invent unrelated metadata."""
    error = provider_stream_error("OpenAI response failed", {})

    assert str(error) == "OpenAI response failed: unknown reason"
    assert error.status_code is None
    assert error.code is None
    assert error.type is None
    assert error.request_id is None
    assert error.response_id is None


def test_provider_stream_error_ignores_invalid_status_and_prefers_status_code() -> None:
    """Only integer status values are accepted, with status_code taking precedence."""
    invalid = provider_stream_error(
        "OpenAI response failed", {"message": "failed", "status": True}
    )
    preferred = provider_stream_error(
        "OpenAI response failed",
        {"message": "failed", "status_code": 503, "status": 429},
    )

    assert invalid.status_code is None
    assert preferred.status_code == 503


def test_failed_responses_result_preserves_structured_error_metadata() -> None:
    """An explicit failed result is promoted to the provider-error boundary."""
    response = SimpleNamespace(
        id="resp-123",
        error={
            "message": "request rejected",
            "code": "bad_request",
            "type": "invalid_request_error",
            "status_code": 400,
            "request_id": "req-123",
        },
        status="failed",
    )

    with pytest.raises(ProviderStreamError) as caught:
        ensure_successful_responses_result(response)

    error = caught.value
    assert str(error) == "OpenAI response failed: request rejected"
    assert error.status_code == 400
    assert error.code == "bad_request"
    assert error.type == "invalid_request_error"
    assert error.request_id == "req-123"
    assert error.response_id == "resp-123"


@pytest.mark.parametrize(
    ("details", "expected_reason"),
    [
        (SimpleNamespace(reason="max_output_tokens"), "max_output_tokens"),
        (None, "unknown reason"),
        (SimpleNamespace(reason="   "), "unknown reason"),
    ],
)
def test_incomplete_responses_result_handles_optional_reason(
    details: object | None, expected_reason: str
) -> None:
    """Incomplete results deterministically normalize present and absent reasons."""
    response = SimpleNamespace(
        id="resp-incomplete",
        error=None,
        status="incomplete",
        incomplete_details=details,
    )

    with pytest.raises(ProviderStreamError) as caught:
        ensure_successful_responses_result(response)

    error = caught.value
    assert str(error) == f"OpenAI response incomplete: {expected_reason}"
    assert error.code == expected_reason
    assert error.type == "incomplete"
    assert error.response_id == "resp-incomplete"


def test_unknown_response_status_is_rejected_with_safe_structured_error() -> None:
    """Unexpected terminal statuses are not silently treated as success."""
    response = {"id": "resp-odd", "error": None, "status": "cancelled"}

    with pytest.raises(ProviderStreamError) as caught:
        ensure_successful_responses_result(response)

    error = caught.value
    assert str(error) == "OpenAI response ended with status cancelled"
    assert error.code == "cancelled"
    assert error.type == "response_status"
    assert error.response_id == "resp-odd"


@pytest.mark.parametrize(
    ("cause_name", "expected_category"),
    [
        ("ReadTimeout", "timeout"),
        ("SSLError", "tls"),
        ("DNSLookupError", "dns"),
        ("NetworkError", "connection"),
        ("UnexpectedFailure", "other"),
    ],
)
def test_provider_error_metadata_classifies_root_causes(
    cause_name: str, expected_category: str
) -> None:
    """Wrapped failures expose only a bounded root-cause type and category."""
    error = Exception("provider failed")
    cause_type = type(cause_name, (Exception,), {})
    error.__cause__ = cause_type("sensitive nested detail")

    metadata = provider_error_metadata(error)

    assert metadata["message"] == "provider failed"
    assert metadata["root_cause_type"] == cause_name
    assert metadata["root_cause_category"] == expected_category
    assert "sensitive nested detail" not in json.dumps(metadata)


def test_provider_error_metadata_preserves_safe_fields_and_redacts_credentials() -> None:
    """Useful diagnostics survive while credential-shaped material is redacted."""
    error = Exception(
        "Authorization: Bearer secret-token api_key=other-secret "
        "https://user:password@example.test/path?access_token=url-secret "
        "sk-abcdefghijklmnop"
    )
    error.status_code = 429
    error.code = "rate_limit_exceeded"
    error.type = "rate_limit_error"
    error.request_id = "req-123"
    error.response_id = "resp-123"

    metadata = provider_error_metadata(error)

    assert metadata["status_code"] == 429
    assert metadata["code"] == "rate_limit_exceeded"
    assert metadata["provider_error_type"] == "rate_limit_error"
    assert metadata["provider_request_id"] == "req-123"
    assert metadata["provider_response_id"] == "resp-123"
    assert "secret-token" not in metadata["message"]
    assert "other-secret" not in metadata["message"]
    assert "password" not in metadata["message"]
    assert "url-secret" not in metadata["message"]
    assert "sk-abcdefghijklmnop" not in metadata["message"]
    assert "[redacted]" in metadata["message"]


def test_blank_unknown_exception_uses_type_name_without_inventing_metadata() -> None:
    """An otherwise empty unexpected exception has a stable generic diagnostic."""
    error = RuntimeError()

    metadata = provider_error_metadata(error)

    assert metadata == {"message": "RuntimeError"}
    assert provider_user_message(error) == "RuntimeError"


def test_provider_user_message_includes_only_supported_diagnostic_fields() -> None:
    """The user-facing form includes status, code and request id but not response body."""
    error = Exception("provider rejected request")
    error.status_code = 403
    error.code = "policy_denied"
    error.request_id = "req-safe"
    error.response_id = "resp-diagnostic-only"

    message = provider_user_message(error)

    assert message == (
        "provider rejected request (HTTP 403, code policy_denied, request req-safe)"
    )
    assert "resp-diagnostic-only" not in message


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "invalid_auth"),
        (403, "provider_forbidden"),
        (429, "provider_rate_limited"),
        (500, "provider_unavailable"),
        (503, "provider_unavailable"),
        (400, "provider_error"),
        (True, "provider_error"),
        (None, "provider_error"),
    ],
)
def test_config_provider_error_classification_by_status(
    status: object | None, expected: str
) -> None:
    """HTTP status classification is stable and rejects bool-as-int status values."""
    error = Exception("provider failure")
    error.status_code = status

    assert classify_config_provider_error(error) == expected


def test_transport_errors_have_safe_messages_and_cannot_connect_classification() -> None:
    """Raw transport details are replaced before entering the provider error path."""
    timeout = provider_transport_error(TimeoutError("socket detail"))
    connection = provider_transport_error(ConnectionError("host detail"))

    assert isinstance(timeout, ProviderTransportError)
    assert str(timeout) == "Provider request timed out"
    assert classify_config_provider_error(timeout) == "cannot_connect"
    assert str(connection) == "Could not connect to provider"
    assert classify_config_provider_error(connection) == "cannot_connect"
    assert "socket detail" not in str(timeout)
    assert "host detail" not in str(connection)


def test_request_reauthentication_rejects_non_auth_and_missing_entry() -> None:
    """Only authentication failures with a config entry start reauthentication."""
    non_auth = Exception("forbidden")
    non_auth.status_code = 403
    auth = Exception("unauthorized")
    auth.status_code = 401

    assert request_reauthentication(object(), object(), non_auth) is False
    assert request_reauthentication(object(), None, auth) is False


def test_request_reauthentication_starts_flow_for_401() -> None:
    """A normalized 401 requests reauthentication exactly once."""
    hass = object()
    entry = SimpleNamespace(async_start_reauth=Mock())
    error = Exception("unauthorized")
    error.status_code = 401

    assert request_reauthentication(hass, entry, error) is True
    entry.async_start_reauth.assert_called_once_with(hass)


def test_request_reauthentication_handles_start_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failure to start the HA flow is contained and logged at the boundary."""
    entry = SimpleNamespace(async_start_reauth=Mock(side_effect=RuntimeError("boom")))
    error = Exception("unauthorized")
    error.status_code = 401

    with caplog.at_level(logging.ERROR):
        result = request_reauthentication(object(), entry, error)

    assert result is False
    assert "Unable to start Extended OpenAI reauthentication flow" in caplog.text


def test_log_provider_failure_serializes_only_safe_metadata() -> None:
    """The logging boundary emits the same redacted metadata contract."""
    logger = Mock(spec=logging.Logger)
    error = Exception("api-key: super-secret")
    error.status_code = 500

    log_provider_failure(logger, "request failed", error)

    logger.error.assert_called_once()
    arguments = logger.error.call_args.args
    assert arguments[0] == "%s: %s"
    assert arguments[1] == "request failed"
    payload = json.loads(arguments[2])
    assert payload["status_code"] == 500
    assert "super-secret" not in payload["message"]
    assert "[redacted]" in payload["message"]
