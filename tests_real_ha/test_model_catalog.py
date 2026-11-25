"""Model data management through HA's registered admin WebSocket boundary."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    backup,
    model_catalog as data,
    model_catalog_manager as runtime,
    restore_recovery,
    transfer,
)
from custom_components.extended_openai_conversation_responses.helpers import (
    get_reasoning_effort_options,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    BUNDLED_CATALOG,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_backup_transfer_protocol import _user_token
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.fixture
async def expanded_catalogue(hass):
    entry = _make_entry(
        conversation_options={
            "chat_model": "gpt-4o",
            "api_mode": "responses",
            "max_tokens": 500,
        }
    )
    await _setup_entry(hass, entry)
    manager = hass.data[runtime.DATA_MANAGER]
    expanded = deepcopy(BUNDLED_CATALOG)
    expanded["catalog_version"] += 1
    model = next(item for item in expanded["models"] if item["id"] == "gpt-4o")
    model["limits"]["max_output_tokens"] = 32768
    await manager._save(expanded, None, None, manager.last_checked)
    manager.catalog = expanded
    data.activate_catalog(expanded)
    try:
        yield entry, manager
    finally:
        data.activate_catalog(None)


def _pause_catalogue_save(manager, monkeypatch):
    entered, resume = asyncio.Event(), asyncio.Event()
    save = manager.store.async_save

    async def pause(value):
        entered.set()
        await resume.wait()
        await save(value)

    monkeypatch.setattr(manager.store, "async_save", pause)
    return entered, resume


@pytest.mark.parametrize("flow", ["create", "advanced_reconfigure"])
async def test_native_ai_task_write_cannot_overlap_catalogue_reset(
    hass, monkeypatch, expanded_catalogue, flow
):
    entry, manager = expanded_catalogue
    before = {key: dict(item.data) for key, item in entry.subentries.items()}
    submission = {"chat_model": "gpt-4o", "api_mode": "responses", "max_tokens": 20000}
    if flow == "create":
        form = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "ai_task_data"),
            context={"source": SOURCE_USER},
        )
        submission["name"] = "Catalogue race"
    else:
        task_id = next(
            key
            for key, item in entry.subentries.items()
            if item.subentry_type == "ai_task_data"
        )
        form = await entry.start_subentry_reconfigure_flow(hass, task_id)
        advanced = await hass.config_entries.subentries.async_configure(
            form["flow_id"],
            {**submission, "advanced_options": True},
        )
        assert advanced["step_id"] == "advanced"
        submission = {}

    entered, resume = _pause_catalogue_save(manager, monkeypatch)
    reset = asyncio.create_task(manager.async_reset())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        result = await hass.config_entries.subentries.async_configure(
            form["flow_id"],
            submission,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"]["base"] == "invalid_request"
        assert (
            "Model data is being updated"
            in result["description_placeholders"]["reason"]
        )
        assert {
            key: dict(item.data) for key, item in entry.subentries.items()
        } == before
    finally:
        resume.set()
        assert (await reset)["source"] == "bundled"
    if flow == "create":
        result = await hass.config_entries.subentries.async_configure(
            form["flow_id"],
            {**submission, "max_tokens": 600},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["max_tokens"] == 600


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("first", ["catalogue", "restore"])
async def test_private_restore_and_catalogue_publication_exclude_each_other(
    hass, monkeypatch, expanded_catalogue, first
):
    entry, manager = expanded_catalogue
    expanded = manager.catalog
    child = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    snapshot = await backup.async_collect_backup_snapshot(hass, entry, child)
    snapshot["agent"]["config"]["max_tokens"] = 20000
    document = backup.finalize_backup_snapshot(snapshot)["document"]
    store = restore_recovery._journal_store(hass, entry.entry_id, child.subentry_id)
    precondition = AsyncMock()

    async def restore():
        return await transfer.async_restore_transfer(
            hass,
            entry,
            child,
            document,
            sections=[transfer.SECTION_CONFIGURATION],
            precondition=precondition,
        )

    if first == "catalogue":
        entered, resume = _pause_catalogue_save(manager, monkeypatch)
        reset = asyncio.create_task(manager.async_reset())
        try:
            await asyncio.wait_for(entered.wait(), 10)
            with pytest.raises(backup.BackupError, match="Model data is being updated"):
                await restore()
            precondition.assert_not_awaited()
            assert await store.async_load() is None
        finally:
            resume.set()
            assert (await reset)["source"] == "bundled"
        assert child.data["max_tokens"] == 500
    else:
        discovered, resume_reset = asyncio.Event(), asyncio.Event()
        journaling, resume_restore = asyncio.Event(), asyncio.Event()
        validate = manager._candidate_preserves_saved_requests
        write = restore_recovery._async_write_journal_verified

        async def pause_validation(*args):
            result = await validate(*args)
            discovered.set()
            await resume_reset.wait()
            return result

        async def pause_journal(*args):
            journaling.set()
            await resume_restore.wait()
            return await write(*args)

        monkeypatch.setattr(
            manager, "_candidate_preserves_saved_requests", pause_validation
        )
        monkeypatch.setattr(
            restore_recovery, "_async_write_journal_verified", pause_journal
        )
        reset = asyncio.create_task(manager.async_reset())
        restore_task = None
        try:
            await asyncio.wait_for(discovered.wait(), 10)
            restore_task = asyncio.create_task(restore())
            await asyncio.wait_for(journaling.wait(), 10)
            resume_reset.set()
            result = await asyncio.wait_for(asyncio.shield(reset), 10)
            assert result["last_error"]
            assert manager.catalog == expanded
        finally:
            resume_reset.set()
            resume_restore.set()
            await reset
            if restore_task is not None:
                assert (await restore_task)["status"] == "restored"
        assert await store.async_load() is None
        assert child.data["max_tokens"] == 20000
    assert (
        build_provider_request_snapshot(child.data, entry.data).api_kwargs[
            "max_output_tokens"
        ]
        == child.data["max_tokens"]
    )


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


async def test_reset_keeps_a_saved_implicit_azure_route_usable(
    hass, hass_ws_client, monkeypatch
):
    """A registered reset cannot strand a new conversation after a real reload."""
    from custom_components.extended_openai_conversation_responses import (
        model_catalog as data,
    )
    from custom_components.extended_openai_conversation_responses.management_ui import (
        WS_COMMAND,
    )
    from custom_components.extended_openai_conversation_responses.request import (
        build_provider_request_snapshot,
    )
    from custom_components.extended_openai_conversation_responses.request_rules import (
        RequestRuleRuntime,
        async_evaluate_rule,
        async_get_request_rules,
    )

    target = "download-only-router-model"
    entry = _make_entry(
        include_ai_task=False,
        data={"api_provider": "azure", "api_version": "2025-04-01-preview"},
        base_url="https://eoai-catalog-test.openai.azure.com/openai/v1",
        conversation_options={
            "chat_model": "base-deployment",
            "azure_model": "gpt-4.1-mini",
            "api_mode": "responses",
            "functions": [],
            "memory_mode": "off",
            "temporary_memory": "off",
            "max_function_calls_per_conversation": 0,
            "max_tokens": 512,
        },
    )
    await _setup_entry(hass, entry)
    subentry = next(iter(entry.subentries.values()))
    admin = await hass_ws_client(
        hass, await _user_token(hass, MockUser(id="catalog-route-admin", is_owner=True))
    )
    value = deepcopy(BUNDLED_CATALOG)
    value["catalog_version"] += 1
    model = deepcopy(
        next(item for item in value["models"] if item["id"] == "gpt-4.1-mini")
    )
    model.update(id=target, display_name="Downloaded routing model")
    value["models"].append(model)

    async def chunks(_size):
        yield json.dumps(value).encode()

    context = AsyncMock()
    context.__aenter__.return_value = SimpleNamespace(
        status=200, headers={}, content=SimpleNamespace(iter_chunked=chunks)
    )
    monkeypatch.setattr(
        runtime,
        "async_get_clientsession",
        lambda _: SimpleNamespace(get=Mock(return_value=context)),
    )

    async def catalog(action):
        await admin.send_json_auto_id({"type": runtime.WS_CATALOG, "action": action})
        return await admin.receive_json()

    async def manage(action, **fields):
        await admin.send_json_auto_id(
            {
                "type": WS_COMMAND,
                "section": "request_rules",
                "action": action,
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                **fields,
            }
        )
        result = await admin.receive_json()
        assert result["success"], result
        return result["result"]

    async def fresh_dispatch():
        current = hass.config_entries.async_get_entry(entry.entry_id)
        options = current.subentries[subentry.subentry_id].data
        rules = await async_get_request_rules(
            hass, entry.entry_id, subentry.subentry_id
        )
        route = RequestRuleRuntime()
        result = await async_evaluate_rule(
            hass,
            rules,
            route,
            "route",
            "new-conversation",
            options["chat_model"],
            request_options=options,
            entry_data=current.data,
        )
        effective = route.effective_options(
            options, "new-conversation", result.request_override
        )
        sent = build_provider_request_snapshot(
            effective, current.data, tools_required=False
        ).api_kwargs
        assert sent["model"] == target
        assert "reasoning" not in sent

    try:
        assert (await catalog("check"))["success"]
        assert (await catalog("apply"))["success"]
        created = await manage(
            "create",
            rule={
                "name": "Downloaded route",
                "phrases": ["route"],
                "match_type": "equals",
                "action_type": "model_routing",
                "action": {
                    "model": target,
                    "scope": "conversation",
                    "continue_to_ai": True,
                },
            },
        )
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        await fresh_dispatch()
        generation = data.catalog_generation()
        blocked = await catalog("reset")
        assert blocked["success"] is False, blocked
        assert blocked["error"]["code"] == "model_catalog_reset_failed"
        assert data.catalog_generation() == generation
        persisted = runtime.ModelCatalogManager(hass)
        await persisted.async_load()
        assert persisted.catalog == value
        await fresh_dispatch()

        rule = created["rule"]
        await manage(
            "update",
            rule_id=rule["id"],
            rule={
                **rule,
                "action": {**rule["action"], "azure_model": target},
            },
        )
        assert (await catalog("reset"))["success"]
        await fresh_dispatch()
    finally:
        data.activate_catalog(None)
