"""Model data management through HA's registered admin WebSocket boundary."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.helpers import (
    get_reasoning_effort_options,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    BUNDLED_CATALOG,
)
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_backup_transfer_protocol import _user_token


@pytest.mark.parametrize("azure", [False, True])
async def test_admin_update_reload_and_reset_using_registered_command(
    hass, hass_ws_client, monkeypatch, azure
):
    entry = _make_entry(
        include_ai_task=False,
        **(
            {
                "data": {"api_provider": "azure", "api_version": "2025-04-01-preview"},
                "base_url": "https://eoai-catalog-test.openai.azure.com/openai/v1",
                "conversation_options": {
                    "chat_model": "base-deployment",
                    "azure_model": "gpt-4.1-mini",
                    "api_mode": "responses",
                    "functions": [],
                    "memory_mode": "off",
                    "temporary_memory": "off",
                    "max_function_calls_per_conversation": 0,
                },
            }
            if azure
            else {}
        ),
    )
    await _setup_entry(hass, entry)
    if azure:
        from custom_components.extended_openai_conversation_responses.request_rules import (
            async_get_request_rules,
        )

        subentry = next(iter(entry.subentries.values()))
        rules = await async_get_request_rules(
            hass, entry.entry_id, subentry.subentry_id
        )
        await rules.async_create(
            {
                "name": "Routed o3-pro",
                "phrases": ["use the pro deployment"],
                "match_type": "equals",
                "action_type": "model_routing",
                "action": {
                    "model": "pro-deployment",
                    "azure_model": "o3-pro",
                    "reasoning_effort": "high",
                    "scope": "conversation",
                    "continue_to_ai": True,
                },
            }
        )
    admin = await hass_ws_client(
        hass, await _user_token(hass, MockUser(id="catalog-admin", is_owner=True))
    )
    non_admin = await hass_ws_client(
        hass, await _user_token(hass, MockUser(id="catalog-viewer"))
    )
    value = deepcopy(BUNDLED_CATALOG)
    value["catalog_version"] += 1
    next(item for item in value["models"] if item["id"] == "gpt-5.6")["reasoning"][
        "efforts"
    ].append("minimal")
    next(item for item in value["models"] if item["id"] == "gpt-5.6")["reasoning"][
        "by_api"
    ]["responses"]["efforts"].append("minimal")

    async def chunks(_size):
        yield json.dumps(value).encode()

    context = AsyncMock()
    context.__aenter__.return_value = SimpleNamespace(
        status=200,
        headers={"ETag": '"catalog-2"'},
        content=SimpleNamespace(iter_chunked=chunks),
    )
    get = Mock(return_value=context)
    monkeypatch.setattr(
        runtime, "async_get_clientsession", lambda _: SimpleNamespace(get=get)
    )

    async def command(client, action):
        await client.send_json_auto_id(
            {"type": runtime.WS_CATALOG, "action": action, "model": "gpt-5.6"}
        )
        return await client.receive_json()

    denied = await command(non_admin, "check")
    assert denied["success"] is False
    get.assert_not_called()

    checked = await command(admin, "check")
    assert checked["success"] is True
    assert checked["result"]["source"] == "bundled"
    assert checked["result"]["update_available"] is True
    assert checked["result"]["available_catalog_version"] == value["catalog_version"]
    assert checked["result"]["reasoning_effort_options"][-1] == "max"
    assert get_reasoning_effort_options("gpt-5.6")[-1] == "max"

    applied = await command(admin, "apply")
    assert applied["success"] is True
    assert applied["result"]["source"] == "downloaded"
    assert applied["result"]["update_available"] is False
    assert applied["result"]["reasoning_effort_options"][-1] == "minimal"
    assert get_reasoning_effort_options("gpt-5.6")[-1] == "minimal"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert (await command(admin, "lookup"))["result"]["reasoning_effort_options"][
        -1
    ] == "minimal"

    # A fresh manager reads the real HA Store, rather than an in-memory test store.
    restarted = runtime.ModelCatalogManager(hass)
    await restarted.async_load()
    assert restarted.catalog == value
    assert restarted.available_catalog is None
    assert restarted.etag == '"catalog-2"'

    denied = await command(non_admin, "reset")
    assert denied["success"] is False
    reset = await command(admin, "reset")
    assert reset["success"] is True
    assert reset["result"]["source"] == "bundled"
    assert reset["result"]["update_available"] is True
    assert reset["result"]["reasoning_effort_options"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]

    restarted = runtime.ModelCatalogManager(hass)
    await restarted.async_load()
    assert restarted.catalog is None
    assert restarted.available_catalog == value
    assert restarted.etag == '"catalog-2"'

    # The registered schema accepts provider-aware lookups without leaking the
    # Azure effort profile into the shared OpenAI catalogue or another lookup.
    for provider, expected in [("azure", ["low", "medium", "high"]), ("openai", [])]:
        await admin.send_json_auto_id(
            {
                "type": runtime.WS_CATALOG,
                "action": "lookup",
                "model": "o3-pro",
                "provider": provider,
            }
        )
        response = await admin.receive_json()
        assert response["success"] is True
        assert response["result"]["reasoning_effort_options"] == expected
        assert response["result"]["model_capabilities"]["uses_reasoning_tokens"] is True
    assert get_reasoning_effort_options("o3-pro") == []
