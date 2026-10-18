from copy import deepcopy
from unittest.mock import AsyncMock

from custom_components.extended_openai_conversation_responses import (
    model_catalog as catalog,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.model_catalog_manager import (
    ModelCatalogManager,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)


async def test_applied_catalog_changes_omitted_effort_but_not_explicit_effort(hass):
    catalog.activate_catalog(None)
    raw = {"chat_model": "gpt-6-astra", "api_mode": "responses", "functions": "[]"}
    cached_editor = agent_config_snapshot(raw)
    before = build_provider_request_snapshot(raw, {}, tools_required=False)
    candidate = deepcopy(catalog.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    next(m for m in candidate["models"] if m["id"] == "gpt-6-astra")[
        "recommended_profile"
    ]["reasoning_effort"] = "high"
    manager = ModelCatalogManager(hass)
    manager.available_catalog = candidate
    manager._save = AsyncMock()
    hass.config_entries.async_entries.return_value = []
    try:
        result = await manager.async_apply_update()
        assert result["last_error"] is None
        after = build_provider_request_snapshot(raw, {}, tools_required=False)
        explicit = build_provider_request_snapshot(
            {**raw, "reasoning_effort": "low"}, {}, tools_required=False
        )
        refreshed_editor = agent_config_snapshot(raw)
        assert cached_editor["reasoning_effort"] == "low"
        assert before.api_kwargs["reasoning"]["effort"] == "low"
        assert after.api_kwargs["reasoning"]["effort"] == "high"
        assert refreshed_editor["reasoning_effort"] == "high"
        assert explicit.api_kwargs["reasoning"]["effort"] == "low"
        assert "reasoning_effort" not in raw
    finally:
        catalog.activate_catalog(None)
