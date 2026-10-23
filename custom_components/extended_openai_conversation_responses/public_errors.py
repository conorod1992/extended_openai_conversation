"""Public errors must not contain backend configuration or template source."""

from http import HTTPStatus

from homeassistant.exceptions import HomeAssistantError, TemplateError

from .exceptions import EffectReplayBlocked, FunctionResponseParseError


class SanitizedTemplateError(HomeAssistantError):
    """Safe feedback for a template failure, including strict execution paths."""

    def __init__(self) -> None:
        super().__init__(
            "Template rendering failed. Check the tool template and argument types."
        )


class SanitizedBackendError(HomeAssistantError):
    """A backend failed; its arbitrary exception payload is private."""

    def __init__(self) -> None:
        super().__init__(
            "Function execution failed. Check the tool configuration and permissions."
        )


class SanitizedRequestError(HomeAssistantError):
    """Preserve bounded HTTP status information without remote prose."""

    def __init__(self, status: int) -> None:
        message = "REST request failed. Check remote service availability."
        if type(status) is int and 100 <= status <= 599:
            try:
                reason = HTTPStatus(status).phrase
            except ValueError:
                reason = "Unsuccessful response"
            message = f"REST request failed: HTTP {status} {reason}"
        super().__init__(message)


def sanitize_backend_error(error: BaseException) -> BaseException:
    public = sanitize_template_error(error)
    if public is not error or isinstance(
        error, (SanitizedTemplateError, SanitizedBackendError, SanitizedRequestError)
    ):
        return public
    # These types control recovery and must retain their semantics through
    # nested/composite calls. Reconstruct them using fixed, source-free text.
    if isinstance(error, EffectReplayBlocked):
        return EffectReplayBlocked(
            "A prior effect was not acknowledged, so this operation cannot safely repeat."
        )
    if isinstance(error, FunctionResponseParseError):
        return FunctionResponseParseError("REST response parsing failed")
    if isinstance(error, HomeAssistantError):
        return SanitizedBackendError()
    return error


def sanitize_template_error(error: BaseException) -> BaseException:
    """Contain wrapped HA template errors without guessing credential formats."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TemplateError):
            return SanitizedTemplateError()
        current = current.__cause__ or current.__context__
    return error
