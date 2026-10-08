"""Structural schema interfaces across Home Assistant's schema migration.

HA 2026.10 annotates its boundaries with probatio classes, while its runtime
continues accepting the voluptuous validators used by older supported releases.
Keep that compatibility localized without changing validators or exception types.
"""

from collections.abc import Callable
from typing import Any, Protocol, cast

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.const import WebSocketCommandHandler


class SchemaValidator(Protocol):
    """The validation operation shared by both schema engines."""

    def __call__(self, data: Any) -> Any: ...


class ExtendableSchema(SchemaValidator, Protocol):
    """A function configuration schema with the shared extension operation."""

    def extend(self, schema: dict[Any, Any]) -> ExtendableSchema: ...


def schema_for_ha(schema: SchemaValidator) -> Any:
    """Pass a supported validator through HA's narrower nominal annotations."""
    return schema


def websocket_command(
    schema: dict[Any, Any] | vol.All,
) -> Callable[[WebSocketCommandHandler], WebSocketCommandHandler]:
    """Retain HA's real compiler for commands using legacy schema markers."""
    return websocket_api.websocket_command(cast(Any, schema))
