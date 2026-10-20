"""Azure routing deployments retain their underlying model during catalogue reset."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.extended_openai_conversation_responses import (
    model_catalog as data,
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    validate_saved_model_routes,
)
from tests.test_model_catalog_transitions import MemoryStore, reset_catalog  # noqa: F401


@pytest.mark.parametrize("explicit", [True, False])
async def test_azure_routing_deployment_does_not_block_bundled_reset(
    hass, monkeypatch, explicit
):
    options = {
        "chat_model": "home-deployment",
        "azure_model": "gpt-5.6",
        "reasoning_effort": "low",
        "max_function_calls_per_conversation": 0,
        "api_mode": "responses",
        "continue_conversation": "ha_default",
    }
    action = {"reasoning_effort": "high", "scope": "conversation"}
    if explicit:
        action["model"] = "home-deployment"
    rules = [{"enabled": True, "action_type": "model_routing", "action": action}]
    entry = SimpleNamespace(
        entry_id="azure",
        title="Azure",
        data={"api_provider": "azure"},
        subentries={
            "agent": SimpleNamespace(
                subentry_id="agent",
                title="Azure agent",
                subentry_type="conversation",
                data=options,
            )
        },
    )
    hass.config_entries.async_entries.return_value = [entry]
    monkeypatch.setattr(
        runtime,
        "async_get_request_rules",
        AsyncMock(return_value=SimpleNamespace(snapshot=lambda: {"rules": rules})),
    )
    validate_saved_model_routes(rules, options, entry.data)
    manager = runtime.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    candidate = deepcopy(data.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    candidate["models"][0]["display_name"] = "Display label only"
    manager.available_catalog = candidate
    assert (await manager.async_apply_update())["source"] == "downloaded"
    assert await manager._candidate_preserves_saved_requests(data.BUNDLED_CATALOG)
    reset = await manager.async_reset()
    assert reset["source"] == "bundled", reset
    assert manager.catalog is None
