"""Bounded Chat delta attribution shared by output admission and accumulation."""

from dataclasses import dataclass
from typing import Any, NoReturn

from .provider_errors import ProviderStreamError

type ChatToolKey = tuple[int, int]


def _ambiguous() -> NoReturn:
    raise ProviderStreamError(
        "Provider returned ambiguous unindexed Chat tool calls",
        error_type="invalid_event_sequence",
    )


@dataclass(frozen=True)
class ChatToolDelta:
    key: ChatToolKey
    id: str
    name: str
    arguments: str
    identity_characters: int


class ChatToolCallResolver:
    """Keep indexed fragments literal; accept unindexed stable-ID envelopes.

    For an unindexed envelope, a nonempty ID and name identify a call. Repeated
    envelopes may repeat that exact ID/name or omit the name; their arguments
    are still deltas. Anonymous fragments are safe only with one known call,
    considering the entire chunk. We never infer an active/newest call, infer
    identity completeness from JSON, or deduplicate indexed identity fragments.
    """

    def __init__(self, max_items: int) -> None:
        self._max_items = max_items
        self._keys: set[ChatToolKey] = set()
        self._anchors: dict[str, tuple[ChatToolKey, str]] = {}
        self._started: set[ChatToolKey] = set()

    def _register(self, key: ChatToolKey) -> None:
        self._keys.add(key)
        if len(self._keys) > self._max_items:
            raise ProviderStreamError(
                "Provider output exceeded local safety limits",
                error_type="output_limit_exceeded",
            )

    def resolve(self, calls: list[Any]) -> list[ChatToolDelta]:
        # Register identities first: an anonymous fragment cannot be assigned to
        # the first call merely because a second envelope is later in this chunk.
        for call in calls:
            index = getattr(call, "index", None)
            if index is not None:
                if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                    _ambiguous()
                self._register((0, index))
            elif call.id and call.id not in self._anchors:
                name = getattr(call.function, "name", None)
                if not name:
                    _ambiguous()
                key = (1, len(self._anchors))
                self._register(key)
                self._anchors[call.id] = (key, name)

        resolved = []
        for call in calls:
            index = getattr(call, "index", None)
            call_id = call.id or ""
            name = getattr(call.function, "name", None) or ""
            identity_characters = len(call_id) + len(name)
            if index is not None:
                key = (0, index)
            elif call_id:
                key, expected_name = self._anchors[call_id]
                if name and name != expected_name:
                    _ambiguous()
                if key in self._started:
                    call_id, name = "", ""
                self._started.add(key)
            else:
                if len(self._keys) != 1:
                    _ambiguous()
                key = next(iter(self._keys))
            resolved.append(
                ChatToolDelta(
                    key,
                    call_id,
                    name,
                    getattr(call.function, "arguments", None) or "",
                    identity_characters,
                )
            )
        return resolved
