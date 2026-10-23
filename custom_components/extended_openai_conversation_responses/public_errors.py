"""Public errors must not contain backend configuration or template source."""

from homeassistant.exceptions import HomeAssistantError, TemplateError


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


def sanitize_backend_error(error: BaseException) -> BaseException:
    public = sanitize_template_error(error)
    if public is not error or isinstance(
        error, (SanitizedTemplateError, SanitizedBackendError)
    ):
        return public
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
