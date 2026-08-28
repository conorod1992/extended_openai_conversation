"""Optional Home Assistant local-intent fallback for conversation requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from hassil.recognize import RecognizeResult

from homeassistant.components import conversation
from homeassistant.components.conversation import ChatLog, ConversationInput
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, intent as ha_intent

CONF_LOCAL_INTENTS_ENABLED = "local_intents_enabled"
CONF_LOCAL_INTENT_EXCLUSIONS = "local_intent_exclusions"
CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI = "local_intent_delayed_commands_to_ai"
DEFAULT_LOCAL_INTENTS_ENABLED = False
DEFAULT_LOCAL_INTENT_EXCLUSIONS: tuple[str, ...] = ()
DEFAULT_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI = False

_FRIENDLY_INTENT_NAMES = {
    "HassBroadcast": "Whole-home broadcast",
    "HassCancelAllTimers": "Cancel all timers",
    "HassCancelTimer": "Cancel a timer",
    "HassClimateGetTemperature": "Check temperature",
    "HassDecreaseTimer": "Reduce a timer",
    "HassGetCurrentDate": "Current date",
    "HassGetCurrentTime": "Current time",
    "HassGetState": "Check device state",
    "HassIncreaseTimer": "Add time to a timer",
    "HassNevermind": "Cancel the current request",
    "HassPauseTimer": "Pause a timer",
    "HassRespond": "Spoken response",
    "HassSetPosition": "Set device position",
    "HassStartTimer": "Start timers and delayed commands",
    "HassStopMoving": "Stop a moving device",
    "HassTimerStatus": "Check a timer",
    "HassToggle": "Toggle devices",
    "HassTurnOff": "Turn off devices",
    "HassTurnOn": "Turn on devices",
    "HassUnpauseTimer": "Resume a timer",
}


@dataclass(frozen=True, slots=True)
class LocalIntentResult:
    """One locally handled Home Assistant intent."""

    response: ha_intent.IntentResponse
    intent_name: str


def _friendly_intent_name(intent_name: str) -> str:
    """Return a readable label without making the catalog depend on a hardcoded list."""
    if label := _FRIENDLY_INTENT_NAMES.get(intent_name):
        return label
    value = intent_name.removeprefix("Hass")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value).strip()
    return value or intent_name


def _has_conversation_command(result: RecognizeResult) -> bool:
    """Return whether a timer intent is carrying a command for later execution."""
    return "conversation_command" in result.entities


def should_handle_locally(
    result: RecognizeResult,
    excluded_intents: Sequence[str],
    delayed_commands_to_ai: bool,
) -> bool:
    """Return whether this already-recognized Home Assistant intent may run locally."""
    intent_name = result.intent.name
    if intent_name in excluded_intents:
        return False
    return not (
        delayed_commands_to_ai
        and intent_name == ha_intent.INTENT_START_TIMER
        and _has_conversation_command(result)
    )


async def async_try_handle_local_intent(
    hass: HomeAssistant,
    user_input: ConversationInput,
    chat_log: ChatLog,
    options: Mapping[str, Any],
    *,
    guest_active: bool,
) -> LocalIntentResult | None:
    """Try Home Assistant's registered intents, returning None when nothing matches."""
    if not options.get(CONF_LOCAL_INTENTS_ENABLED, DEFAULT_LOCAL_INTENTS_ENABLED):
        return None

    # Home Assistant's normal intent engine does not know about this integration's
    # Guest Mode policy. Keep Guest Mode on the existing policy-enforced model path.
    if guest_active:
        return None

    handle_intents = getattr(conversation, "async_handle_intents", None)
    if handle_intents is None:
        return None

    excluded = tuple(options.get(CONF_LOCAL_INTENT_EXCLUSIONS, ()))
    delayed_commands_to_ai = bool(
        options.get(
            CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI,
            DEFAULT_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI,
        )
    )
    accepted_intent: str | None = None

    def intent_filter(result: RecognizeResult) -> bool:
        """Return True when Home Assistant should reject this local intent match."""
        nonlocal accepted_intent
        if not should_handle_locally(result, excluded, delayed_commands_to_ai):
            return True
        accepted_intent = result.intent.name
        return False

    response = await handle_intents(
        hass,
        user_input,
        chat_log,
        intent_filter=intent_filter,
    )
    if response is None:
        return None
    return LocalIntentResult(
        response=response, intent_name=accepted_intent or "unknown"
    )


def registered_intent_catalog(
    hass: HomeAssistant, configured_exclusions: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Return the live registered intent catalog plus saved unavailable exclusions."""
    registered = {
        str(handler.intent_type)
        for handler in ha_intent.async_get(hass)
        if isinstance(getattr(handler, "intent_type", None), str)
        and handler.intent_type
    }
    all_names = registered | {
        name for name in configured_exclusions if isinstance(name, str) and name.strip()
    }
    return [
        {
            "intent": name,
            "label": _friendly_intent_name(name),
            "available": name in registered,
        }
        for name in sorted(
            all_names, key=lambda item: (_friendly_intent_name(item), item)
        )
    ]


def _conversation_entity_id(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> str | None:
    """Resolve the conversation entity belonging to one agent subentry."""
    registry = er.async_get(hass)
    try:
        entries = er.async_entries_for_config_entry(registry, entry_id)
    except AttributeError:
        # Some test/minimal HA contexts create the registry before its backing
        # entity collection is loaded. The warning is diagnostic-only, so fail
        # closed and omit it rather than breaking configuration management.
        return None
    for item in entries:
        if item.config_subentry_id == subentry_id and item.domain == "conversation":
            return item.entity_id
    return None


def _get_assist_pipelines(hass: HomeAssistant) -> list[Any]:
    """Load Assist pipeline state only when the diagnostics snapshot needs it."""
    from homeassistant.components import assist_pipeline

    return list(assist_pipeline.async_get_pipelines(hass))


def conflicting_assist_pipelines(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> list[dict[str, str]]:
    """Return Assist pipelines that can consume local intents before this agent."""
    entity_id = _conversation_entity_id(hass, entry_id, subentry_id)
    if entity_id is None:
        return []
    try:
        pipelines = _get_assist_pipelines(hass)
    except ImportError, KeyError, RuntimeError:
        return []
    return [
        {"id": pipeline.id, "name": pipeline.name}
        for pipeline in pipelines
        if pipeline.conversation_engine == entity_id and pipeline.prefer_local_intents
    ]


def local_handling_snapshot(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    configured_exclusions: Sequence[str] = (),
) -> dict[str, Any]:
    """Return frontend-safe live information for local handling settings."""
    return {
        "supported": callable(getattr(conversation, "async_handle_intents", None)),
        "intents": registered_intent_catalog(hass, configured_exclusions),
        "pipeline_conflicts": conflicting_assist_pipelines(hass, entry_id, subentry_id),
    }
