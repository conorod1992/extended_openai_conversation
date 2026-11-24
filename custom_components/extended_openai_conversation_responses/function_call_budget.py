"""Request-local execution budget for model-requested Function Tools."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.exceptions import HomeAssistantError


class FunctionCallLimitReached(HomeAssistantError):
    """Expected refusal of execution, distinct from backend/provider failure."""


@dataclass(slots=True)
class FunctionCallBudget:
    """Enforce an exact per-conversation Function Tool execution ceiling."""

    limit: int
    used: int = 0

    @property
    def exhausted(self) -> bool:
        """Return whether no further Function Tool execution is permitted."""
        return self.limit >= 0 and self.used >= self.limit

    def claim(self, tool_name: str) -> None:
        """Reserve one execution slot before the tool can run."""
        if self.exhausted:
            raise FunctionCallLimitReached(
                f"Function call limit of {self.limit} reached; refusing to execute "
                f"additional tool `{tool_name}`"
            )
        self.used += 1
