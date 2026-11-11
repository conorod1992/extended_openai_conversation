"""The immediate whole-home Broadcast shortcut, independent of HA intents."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from homeassistant.components.conversation import ConversationInput
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent as ha_intent

from .intercom import async_get_intercom
from .local_intents import CONF_LOCAL_INTENT_EXCLUSIONS, LocalIntentResult


def broadcast_message(text: Any) -> str | None:
    """Extract a literal message; reserved leading syntax falls back normally.

    No destination lookup or language inference belongs in this command. Reserve
    preposition-led arguments and the old "a message" form so obvious target/time
    clauses cannot accidentally become an immediate whole-home announcement.
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0].casefold() != "broadcast":
        return None
    message = parts[1]
    if re.match(r"(?:to|in|at|after|for)\b|a\s+message\b", message, re.IGNORECASE):
        return None
    return message


async def async_try_broadcast_command(
    hass: HomeAssistant,
    user_input: ConversationInput,
    options: Mapping[str, Any],
    *,
    guest_active: bool,
) -> LocalIntentResult | None:
    """Attempt one admission with real caller authority; failures must propagate."""
    # Preserve explicit per-agent opt-outs from 6.8.3 without migrating settings.
    if guest_active or "HassBroadcast" in options.get(CONF_LOCAL_INTENT_EXCLUSIONS, ()):
        return None
    message = broadcast_message(getattr(user_input, "text", None))
    if message is None:
        return None
    manager = await async_get_intercom(hass)
    if not manager.enabled or not manager.builtin_voice_command:
        return None
    await manager.async_send(
        message,
        whole_home=True,
        context=user_input.context,
        origin_entity_id=getattr(user_input, "satellite_id", None),
        origin_device_id=getattr(user_input, "device_id", None),
        source="local_voice",
    )
    response = ha_intent.IntentResponse(language=user_input.language or "en")
    response.async_set_speech("Broadcast queued.")
    return LocalIntentResult(response=response, intent_name="ExtendedBroadcast")
