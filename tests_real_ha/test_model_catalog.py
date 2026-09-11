"""Model data management through HA's registered admin WebSocket boundary."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


async def test_admin_update_reload_and_reset_using_registered_command(
    hass, hass_ws_client, monkeypatch
):
    entry = _make_entry(include_ai_task=False)
    await _setup_entry(hass, entry)
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
    ].append("xhigh")

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

    denied = await command(non_admin, "update")
    assert denied["success"] is False
    get.assert_not_called()
    updated = await command(admin, "update")
    assert updated["success"] is True
    assert updated["result"]["source"] == "downloaded"
    assert updated["result"]["reasoning_effort_options"][-1] == "xhigh"
    assert get_reasoning_effort_options("gpt-5.6")[-1] == "xhigh"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert (await command(admin, "lookup"))["result"]["reasoning_effort_options"][
        -1
    ] == "xhigh"
    # A fresh manager reads the real HA Store, rather than an in-memory test store.
    restarted = runtime.ModelCatalogManager(hass)
    await restarted.async_load()
    assert restarted.catalog == value
    assert restarted.etag == '"catalog-2"'
    denied = await command(non_admin, "reset")
    assert denied["success"] is False
    reset = await command(admin, "reset")
    assert reset["success"] is True
    assert reset["result"]["source"] == "bundled"
    assert reset["result"]["reasoning_effort_options"] == ["low", "medium", "high"]
    restarted = runtime.ModelCatalogManager(hass)
    await restarted.async_load()
    assert restarted.catalog is None
