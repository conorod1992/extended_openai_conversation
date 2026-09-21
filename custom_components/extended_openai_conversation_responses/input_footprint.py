"""Content-free input footprint telemetry for the management Usage page."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .management_request_preview import async_preview_effective_request, entry_and_agent
from .payload_diagnostics import APPROX_TOKEN_METHOD, approximate_tokens
from .usage import async_get_usage

_LATEST_FOOTPRINTS = f"{DOMAIN}.input_footprints"


def _serialized_footprint(
    input_value: Any,
    tools: Any = None,
    *,
    tool_measurement: tuple[int, int] | None = None,
) -> tuple[int, int, int]:
    """Return exact serialized input/tool characters and the truncation estimate."""
    from .context_usage_hardening import measure_provider_input

    return measure_provider_input(
        input_value,
        tools,
        tool_measurement=tool_measurement,
    )


def input_footprint_metrics(
    input_value: Any,
    tools: Any = None,
    *,
    tool_measurement: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Measure locally assembled model input without retaining its content."""
    input_characters, tool_characters, conservative_tokens = _serialized_footprint(
        input_value,
        tools,
        tool_measurement=tool_measurement,
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


def capture_live_footprint(
    entity: Any,
    input_value: Any,
    tools: Any = None,
    *,
    tool_measurement: tuple[int, int] | None = None,
) -> int:
    """Replace the existing context estimate while also retaining only its size."""
    metrics = input_footprint_metrics(
        input_value,
        tools,
        tool_measurement=tool_measurement,
    )
    footprints = entity.hass.data.setdefault(_LATEST_FOOTPRINTS, {})
    footprints[(entity.entry.entry_id, entity.subentry.subentry_id)] = {
        **{
            key: value
            for key, value in metrics.items()
            if key != "context_safety_estimate_tokens"
        },
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
    """Return exact provider input usage for the newest retained request."""
    if not usage.requests:
        return None
    request = usage.requests[-1]
    if request.input_tokens <= 0:
        return None
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
    entry_id = str(message.get("entry_id") or "")
    subentry_id = str(message.get("subentry_id") or "")
    entry, subentry = entry_and_agent(hass, entry_id, subentry_id)
    preview = await async_preview_effective_request(
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
