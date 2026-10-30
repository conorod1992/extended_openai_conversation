"""Transaction-owned failure evidence, independent of exception prose or type."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class MutationFailure:
    outcome: Literal["rejected", "committed", "uncertain"]
    recovery_pending: bool = False


def mark_mutation_failure[E: Exception](
    error: E,
    outcome: Literal["rejected", "committed", "uncertain"],
    *,
    recovery_pending: bool = False,
) -> E:
    """Retain caller-compatible exceptions while carrying explicit write evidence."""
    error.__dict__["_eoai_mutation_failure"] = MutationFailure(
        outcome, recovery_pending
    )
    return error


def mutation_failure(error: Exception) -> MutationFailure | None:
    value = getattr(error, "_eoai_mutation_failure", None)
    return value if isinstance(value, MutationFailure) else None


def mutation_error_code(error: Exception) -> str:
    """Use HA's existing error-code field without changing successful payloads."""
    if (failure := mutation_failure(error)) is None:
        return "invalid_request"
    suffix = "_recovery_pending" if failure.recovery_pending else ""
    return f"eoai_mutation_{failure.outcome}{suffix}"
