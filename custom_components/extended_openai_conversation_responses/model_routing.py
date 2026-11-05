"""Bind an Azure route's deployment and underlying capability identity together."""

from collections.abc import Mapping
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .const import CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL
from .helpers import effective_api_provider
from .model_capabilities import get_model_capabilities


def routed_model_override(
    defaults: Mapping[str, Any],
    model: str | None,
    underlying_model: str | None,
    entry_data: Mapping[str, Any],
    prior: Mapping[str, Any] | None = None,
    *,
    model_is_known: bool | None = None,
) -> dict[str, str]:
    """Resolve the route's binding against current or prospective recognition.

    Catalogue guards supply recognition from the proposed catalogue. Runtime
    callers retain the active lookup; configured and explicit bindings do not
    require an exact catalogue entry.
    """
    override = {CONF_CHAT_MODEL: model} if model else {}
    azure = (
        effective_api_provider(
            entry_data.get("api_provider"), entry_data.get("base_url")
        )
        == "azure"
    )
    if not azure:
        if underlying_model:
            raise HomeAssistantError(
                "An underlying Azure model is only supported for Azure routes"
            )
        return override
    if underlying_model:
        override["azure_model"] = underlying_model
    elif model:
        if model == defaults.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL):
            override["azure_model"] = str(defaults.get("azure_model") or model)
        elif prior and model == prior.get(CONF_CHAT_MODEL) and prior.get("azure_model"):
            override["azure_model"] = str(prior["azure_model"])
        elif (
            model_is_known
            if model_is_known is not None
            else get_model_capabilities(model)["status"] != "unknown"
        ):
            override["azure_model"] = model
        else:
            raise HomeAssistantError(
                "Choose the underlying Azure model for a route to a different custom deployment"
            )
    return override
