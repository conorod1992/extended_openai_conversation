"""Public errors must not contain backend configuration or template source."""

from http import HTTPStatus

from homeassistant.exceptions import HomeAssistantError, TemplateError

from .exceptions import (
    EffectOutcomeUnknown,
    EffectReplayBlocked,
    EntityNotExposed,
    FunctionResponseParseError,
    SQLiteDeadlineExceeded,
    SQLiteResultLimitExceeded,
    TokenLengthExceededError,
)


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


class InvalidIndirectTarget(HomeAssistantError):
    """Source-free feedback that remains useful through backend sanitization."""

    def __init__(self) -> None:
        super().__init__(
            "area_id, device_id, floor_id and label_id must be a string or list "
            "of strings containing exact, nonblank IDs without surrounding "
            "whitespace or commas; multiple IDs require a list of exact IDs"
        )


class SanitizedUnexpectedBackendError(RuntimeError):
    """Keep unexpected failures propagating without exposing dependency prose."""

    def __init__(self) -> None:
        super().__init__(str(SanitizedBackendError()))


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


class InvalidScrapeSelector(HomeAssistantError):
    """Report a configuration error without revealing the authored selector."""

    def __init__(self) -> None:
        super().__init__(
            "Invalid Scrape CSS selector. Check the selector in Functions."
        )


def safe_operational_explanation(error: BaseException) -> str | None:
    """Project only known error contracts, never arbitrary exception messages."""
    from .operational_errors import StorageSerializationFailure
    from .persistence_hardening import StorageRepairRequired
    from .skill_resource_limits import MAX_SKILL_METADATA_BYTES, SkillMetadataError

    if type(error) is StorageSerializationFailure:
        return "Home Assistant could not encode the storage snapshot. Check the affected configuration before retrying."
    if type(error) is TokenLengthExceededError:
        return str(
            TokenLengthExceededError(error.token if type(error.token) is int else None)
        )
    if type(error) is EntityNotExposed and error.reason in {
        "invalid_target",
        "permission",
        "policy",
    }:
        return str(EntityNotExposed("", error.reason))
    if type(error) is InvalidIndirectTarget:
        return str(InvalidIndirectTarget())
    if type(error) is InvalidScrapeSelector:
        return str(InvalidScrapeSelector())
    if type(error) is FunctionResponseParseError:
        return (
            "The REST response could not be parsed. Check the endpoint response format."
        )
    if type(error) is SQLiteDeadlineExceeded:
        return "The SQLite query reached its deadline. Check for database contention or reduce the work performed by the query."
    if type(error) is SQLiteResultLimitExceeded:
        return "The SQLite result exceeded its configured limits. Reduce the rows or columns returned."
    if isinstance(error, TemplateError):
        return str(SanitizedTemplateError())
    if type(error) is SanitizedTemplateError:
        return str(SanitizedTemplateError())
    if type(error) is StorageRepairRequired:
        return "The store is quarantined. Have an administrator repair it or recover a validated backup into a clean assistant."
    if type(error) is EffectOutcomeUnknown:
        return "An action may already have taken effect. Check the actual result before retrying."
    if type(error) is EffectReplayBlocked:
        return "A previous effect is unacknowledged. Check the actual result before retrying."
    if (
        type(error) is SkillMetadataError
        and error.args
        and isinstance(error.args[0], str)
        and error.args[0]
        in {
            "Skill metadata is not a regular file",
            "Unable to read Skill metadata; check the file and its permissions",
            f"Skill metadata exceeds the {MAX_SKILL_METADATA_BYTES}-byte limit",
            "Skill metadata is not valid UTF-8",
            "SKILL.md is outside its Skill directory",
        }
    ):
        return str(error.args[0])
    return None


def sanitize_backend_error(error: BaseException) -> BaseException:
    public = sanitize_template_error(error)
    if public is not error or isinstance(
        error,
        (
            SanitizedTemplateError,
            SanitizedBackendError,
            SanitizedUnexpectedBackendError,
            SanitizedRequestError,
            InvalidIndirectTarget,
            InvalidScrapeSelector,
            EffectOutcomeUnknown,
        ),
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
    if isinstance(error, Exception):
        return SanitizedUnexpectedBackendError()
    return error


def sanitize_template_error(error: BaseException) -> BaseException:
    """Contain wrapped HA template errors without guessing credential formats."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, EffectOutcomeUnknown):
            return error
        if isinstance(current, TemplateError):
            return SanitizedTemplateError()
        current = current.__cause__ or current.__context__
    return error
