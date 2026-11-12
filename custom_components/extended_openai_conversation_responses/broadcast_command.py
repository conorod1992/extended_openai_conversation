"""The immediate whole-home Broadcast shortcut, independent of HA intents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re
from typing import Any

from homeassistant.components.conversation import ConversationInput
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent as ha_intent

from .guest_mode import GUEST_MODE_UNAVAILABLE
from .intercom import async_get_intercom
from .local_intents import CONF_LOCAL_INTENT_EXCLUSIONS, LocalIntentResult


def broadcast_message(text: Any) -> str | None:
    """Extract a literal message; reserved leading syntax falls back normally.

    No destination lookup or language inference belongs in this command. Reserve
    preposition-led arguments and the old "a message" form. Destination-like
    prepositions later in the text also fall back, apart from literal durations.
    """
    if not isinstance(text, str):
        return None
    match = re.fullmatch(
        r"broadcast(?:\s+|[,:;.!?]\s*)(.+)", text.strip(), re.IGNORECASE | re.DOTALL
    )
    if match is None:
        return None
    message = match[1].lstrip()
    if re.match(r"(?:to|in|on|at|after|for)\b|a\s+message\b", message, re.IGNORECASE):
        return None
    # Deliberately restrictive syntax, not destination inference. Only this
    # small duration form is exempt from a later "in"; the message is unchanged.
    # Check the whole suffix so a duration cannot hide a subsequent destination.
    if re.search(
        r"\b(?:to|on|at)\b|\bin\b(?!\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:seconds?|minutes?|hours?)(?=[.!?,;]|$))",
        message,
        re.IGNORECASE,
    ):
        return None
    return message


async def async_try_broadcast_command(
    hass: HomeAssistant,
    user_input: ConversationInput,
    options: Mapping[str, Any],
    *,
    guest_active: bool,
    live_guest_active: Callable[[], bool] | None = None,
) -> LocalIntentResult | None:
    """Attempt one admission with real caller authority; failures must propagate."""
    # Preserve explicit per-agent opt-outs from 6.8.3 without migrating settings.
    if guest_active or "HassBroadcast" in options.get(CONF_LOCAL_INTENT_EXCLUSIONS, ()):
        return None
    message = broadcast_message(getattr(user_input, "text", None))
    if message is None:
        return None

    def require_non_guest() -> None:
        # Request policy can tighten after routing, storage loading or caller
        # authorization yields. Use the caller's existing live/pinned policy.
        if live_guest_active is not None and live_guest_active():
            raise HomeAssistantError(GUEST_MODE_UNAVAILABLE)

    require_non_guest()
    manager = await async_get_intercom(hass)
    require_non_guest()
    if not manager.enabled or not manager.builtin_voice_command:
        return None

    def validate_admission() -> None:
        require_non_guest()
        if not manager.builtin_voice_command:
            raise HomeAssistantError(
                "The built-in Broadcast voice command was disabled before queue admission."
            )

    await manager.async_send(
        message,
        whole_home=True,
        context=user_input.context,
        origin_entity_id=getattr(user_input, "satellite_id", None),
        origin_device_id=getattr(user_input, "device_id", None),
        source="local_voice",
        # This existing synchronous veto runs after CONTROL authorization and
        # before the first queue mutation, with no intervening await. It does
        # not change admission/retry behavior for other Broadcast entry points.
        _admission_started=validate_admission,
    )
    response = ha_intent.IntentResponse(language=user_input.language or "en")
    response.async_set_speech("Broadcast queued.")
    return LocalIntentResult(response=response, intent_name="ExtendedBroadcast")
