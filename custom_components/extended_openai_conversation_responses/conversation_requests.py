"""Request ownership shared by conversation runtimes; no timeout policy."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(eq=False, slots=True)
class ConversationRequestToken[T]:
    """Retain invalidation status even after the runtime releases a request."""

    session_id: str
    generation: T
    ended: bool = False
    refresh_allowed: bool = True


class ConversationRequests[T]:
    """Protect active generations and consume completion rights exactly once."""

    def __init__(self) -> None:
        self._tokens: dict[object, ConversationRequestToken[T]] = {}

    def __len__(self) -> int:
        return len(self._tokens)

    @property
    def owners(self) -> set[str]:
        """Return owners with requests, including requests without saved state."""
        return {token.session_id for token in self._tokens.values()}

    def tokens(self, session_id: str) -> tuple[ConversationRequestToken[T], ...]:
        """Capture ownership for work that may outlive runtime cleanup."""
        return tuple(
            token for token in self._tokens.values() if token.session_id == session_id
        )

    def active(self, session_id: str, generation: T | None = None) -> bool:
        """Check protection, optionally restricted to a state object's identity."""
        return any(
            token.session_id == session_id
            and (generation is None or token.generation is generation)
            for token in self._tokens.values()
        )

    def begin(self, session_id: str, generation: T) -> object:
        """Protect a request's owner and retain its original generation."""
        token = ConversationRequestToken(session_id, generation)
        self._tokens[token] = token
        return token

    def finish(
        self,
        token: object,
        *,
        successful: bool,
        current_generation: Callable[[str], T | None] | None = None,
    ) -> T | None:
        """Consume the token and return only a surviving refreshable generation."""
        request = self._tokens.pop(token, None)
        if (
            request is None
            or not successful
            or not request.refresh_allowed
            or request.ended
            or (
                current_generation is not None
                and current_generation(request.session_id) is not request.generation
            )
        ):
            return None
        return request.generation

    def invalidate(self, session_id: str, *, ended: bool) -> None:
        """Revoke refresh; only an ended conversation loses execution ownership."""
        for token in self.tokens(session_id):
            token.refresh_allowed = False
            if ended:
                token.ended = True
                self._tokens.pop(token)

    def clear(self) -> None:
        """End all generations, including ownership retained by in-flight work."""
        for session_id in self.owners:
            self.invalidate(session_id, ended=True)
