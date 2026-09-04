"""Content-free input footprint telemetry for the management Usage page."""

from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .payload_diagnostics import APPROX_TOKEN_METHOD, approximate_tokens
from .usage import async_get_usage

_LATEST_FOOTPRINTS = f"{DOMAIN}.input_footprints"
_INSTALLED = False

ManagementCommand = Callable[
    [HomeAssistant, str, bool, dict[str, Any]], Awaitable[dict[str, Any]]
]


def _serialized_footprint(input_value: Any, tools: Any = None) -> tuple[int, int, int]:
    """Return exact serialized input/tool characters and the truncation estimate."""
    from . import context_usage_hardening

    input_characters, input_non_ascii = context_usage_hardening._serialized_characters(
        input_value
    )
    tool_characters = 0
    tool_non_ascii = 0
    if tools:
        tool_characters, tool_non_ascii = context_usage_hardening._serialized_characters(
            tools
        )

    total_characters = input_characters + tool_characters
    non_ascii = input_non_ascii + tool_non_ascii
    ascii_characters = max(0, total_characters - non_ascii)
    conservative_tokens = max(1, math.ceil(ascii_characters / 3) + (non_ascii * 2))
    return input_characters, tool_characters, conservative_tokens


def input_footprint_metrics(input_value: Any, tools: Any = None) -> dict[str, Any]:
    """Measure locally assembled model input without retaining its content."""
    input_characters, tool_characters, conservative_tokens = _serialized_footprint(
        input_value, tools
    )
    characters = input_characters + tool_characters
    return {
        "characters": characters,
        "approx_tokens": approximate_tokens(characters),
        "approximation_method": APPROX_TOKEN_METHOD,
        "input_characters": input_characters,
        "tool_characters": tool_characters,
        "context_safety_estimate_tokens": conservative_tokens,
    }


def _capture_live_footprint(input_value: Any, tools: Any = None) -> int:
    """Replace the existing context estimate while also retaining only its size."""
    from . import context_usage_hardening

    state = context_usage_hardening._CURRENT_ESTIMATE_STATE.get()
    if state is None:
        return _ORIGINAL_ESTIMATE(input_value, tools)

    metrics = input_footprint_metrics(input_value, tools)
    entity = state.entity
    footprints = entity.hass.data.setdefault(_LATEST_FOOTPRINTS, {})
    footprints[(entity.entry.entry_id, entity.subentry.subentry_id)] = {
        **{key: value for key, value in metrics.items() if key != "context_safety_estimate_tokens"},
        "captured_at": dt_util.utcnow().isoformat(),
        "attachments_excluded": True,
    }
    # Preserve the pre-existing deliberately conservative truncation fallback exactly.
    return int(metrics["context_safety_estimate_tokens"])


def _baseline_footprint(preview: dict[str, Any]) -> dict[str, Any]:
    """Build a content-free fresh-request baseline from the existing preview."""
    characters = max(0, int(preview.get("total_character_count", 0)))
    savings = preview.get("function_group_savings", {})
    saved_characters = max(0, int(savings.get("characters", 0)))
    without_groups = characters + saved_characters
    return {
        "characters": characters,
        "approx_tokens": approximate_tokens(characters),
        "approximation_method": APPROX_TOKEN_METHOD,
        "without_function_groups_characters": without_groups,
        "without_function_groups_approx_tokens": approximate_tokens(without_groups),
        "function_group_savings": {
            "characters": saved_characters,
            "approx_tokens": approximate_tokens(saved_characters),
            "percent": max(0, int(savings.get("percent", 0))),
        },
        "notes": list(preview.get("notes", [])),
    }


def _latest_provider_usage(usage: Any) -> dict[str, Any] | None:
    """Return only exact provider-reported input usage for the newest retained request."""
    if not usage.requests:
        return None
    request = usage.requests[-1]
    return {
        "timestamp": request.timestamp,
        "input_tokens": request.input_tokens,
        "cached_input_tokens": request.cached_input_tokens,
        "provider": request.provider,
        "model": request.model,
        "api_mode": request.api_mode,
    }


async def async_input_footprint(
    hass: HomeAssistant,
    user_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Return baseline and latest content-free input footprint measurements."""
    from . import management_ui

    entry, subentry = management_ui.entry_and_agent(
        hass, message.get("entry_id"), message.get("subentry_id")
    )
    preview = await management_ui._async_preview_effective_request(
        hass, entry, subentry, dict(subentry.data), user_id
    )
    usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    latest = hass.data.get(_LATEST_FOOTPRINTS, {}).get(
        (entry.entry_id, subentry.subentry_id)
    )
    return {
        "baseline": _baseline_footprint(preview),
        "latest": dict(latest) if isinstance(latest, dict) else None,
        "latest_provider_usage": _latest_provider_usage(usage),
        "notice": (
            "Character counts are measured locally. Approximate token counts use "
            "characters / 4 and are not provider billing tokens; provider-reported "
            "usage remains authoritative."
        ),
    }


def _wrap_management_command(original: ManagementCommand) -> ManagementCommand:
    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        if message.get("section") == "usage" and message.get("action") == "footprint":
            # The existing management-permissions wrapper, installed after this hook,
            # keeps every detailed Usage action admin-only.
            return await async_input_footprint(hass, user_id, message)
        return await original(hass, user_id, is_admin, message)

    return wrapped


def install_input_footprint() -> None:
    """Install footprint capture and the content-free management read once."""
    global _INSTALLED, _ORIGINAL_ESTIMATE
    if _INSTALLED:
        return

    from . import context_usage_hardening, management_ui

    _ORIGINAL_ESTIMATE = context_usage_hardening.estimate_provider_input_tokens
    context_usage_hardening.estimate_provider_input_tokens = _capture_live_footprint
    management_ui.async_management_command = _wrap_management_command(
        management_ui.async_management_command
    )
    _INSTALLED = True


_ORIGINAL_ESTIMATE: Callable[[Any, Any], int]
