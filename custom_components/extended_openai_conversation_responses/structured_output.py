"""Privacy-safe structured-output parsing helpers."""

from __future__ import annotations

from json import JSONDecodeError
import logging
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.json import json_loads

_LOGGER = logging.getLogger(__name__)


def parse_ai_task_structured_response(text: str) -> Any:
    """Parse AI Task JSON without exposing model content in logs."""
    try:
        return json_loads(text)
    except JSONDecodeError as err:
        _LOGGER.error("Failed to parse structured AI Task JSON response: %s", err)
        raise HomeAssistantError("Error with structured response") from err
