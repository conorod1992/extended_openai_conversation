"""Request-local execution budget for model-requested Function Tools."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from homeassistant.exceptions import HomeAssistantError


@dataclass(slots=True)
class FunctionCallBudget:
    """Enforce an exact per-conversation Function Tool execution ceiling."""

    limit: int
    used: int = 0

    @property
    def exhausted(self) -> bool:
        """Return whether no further Function Tool execution is permitted."""
        return self.limit >= 0 and self.used >= self.limit

    @property
    def remaining(self) -> int | None:
        """Return remaining slots, or None when a negative limit is unlimited."""
        if self.limit < 0:
            return None
        return max(0, self.limit - self.used)

    def claim(self, tool_name: str) -> None:
        """Reserve one execution slot before the tool can run."""
        self.claim_many((tool_name,))

    def claim_many(self, tool_names: Iterable[str]) -> None:
        """Atomically reserve a batch so parallel execution cannot overshoot the cap."""
        names = tuple(tool_names)
        if not names:
            return
        if self.limit >= 0 and self.used + len(names) > self.limit:
            allowed = max(0, self.limit - self.used)
            refused = names[min(allowed, len(names) - 1)]
            raise HomeAssistantError(
                f"Function call limit of {self.limit} reached; refusing to execute "
                f"additional tool `{refused}`"
            )
        self.used += len(names)
