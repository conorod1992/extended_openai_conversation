"""Focused tests for integration-specific exception types."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses.exceptions import (
    CallServiceError,
    EntityNotExposed,
    EntityNotFound,
    FunctionLoadFailed,
    FunctionNotFound,
    InvalidFunction,
    NativeNotFound,
    ParseArgumentsFailed,
    TokenLengthExceededError,
)


def test_entity_exceptions_preserve_entity_id_and_messages() -> None:
    missing = EntityNotFound("light.missing")
    assert missing.entity_id == "light.missing"
    assert str(missing) == "Unable to find entity light.missing"

    hidden = EntityNotExposed("lock.front_door")
    assert hidden.entity_id == "lock.front_door"
    assert str(hidden) == "entity lock.front_door is not exposed"


def test_call_service_error_preserves_call_details() -> None:
    data = {"brightness": 100}
    error = CallServiceError("light", "turn_on", data)

    assert error.domain == "light"
    assert error.service == "turn_on"
    assert error.data is data
    assert str(error) == (
        "unable to call service light.turn_on with data {'brightness': 100}. "
        "One of 'entity_id', 'area_id', or 'device_id' is required"
    )


def test_function_lookup_exceptions_preserve_names() -> None:
    function = FunctionNotFound("weather_lookup")
    assert function.function == "weather_lookup"
    assert str(function) == "function 'weather_lookup' does not exist"

    native = NativeNotFound("execute_service")
    assert native.name == "execute_service"
    assert str(native) == "native function 'execute_service' does not exist"


def test_function_load_failed_has_stable_user_message() -> None:
    error = FunctionLoadFailed()
    assert str(error) == (
        "failed to load functions. Verify functions are valid in a yaml format"
    )


def test_parse_arguments_failed_does_not_echo_provider_payload() -> None:
    malformed = '{"entity_id":'
    error = ParseArgumentsFailed(malformed)

    assert error.arguments == malformed
    assert str(error) == (
        "The provider returned malformed or unparseable tool-call arguments."
    )
    assert malformed not in str(error)


def test_token_length_exceeded_preserves_limit() -> None:
    error = TokenLengthExceededError(4096)

    assert error.token == 4096
    assert str(error) == (
        "token length(`4096`) exceeded. Increase maximum token to avoid the issue."
    )


def test_invalid_function_message_includes_chained_cause() -> None:
    cause = ValueError("schema is invalid")
    error = InvalidFunction("calendar_lookup")
    error.__cause__ = cause

    assert error.function_name == "calendar_lookup"
    assert str(error) == (
        "failed to validate function `calendar_lookup` (schema is invalid)"
    )


def test_invalid_function_without_cause_is_still_renderable() -> None:
    error = InvalidFunction("calendar_lookup")
    assert str(error) == "failed to validate function `calendar_lookup` (None)"
