"""Regressions for Azure identity, live area labels, and durable Usage retention."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from openai.types.responses import ResponseOutputMessage, ResponseOutputText
import pytest

from custom_components.extended_openai_conversation_responses import (
    async_migrate_integration,
    helpers,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.config_flow import (
    LEGACY_STEP_USER_DATA_SCHEMA,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONFIG_ENTRY_VERSION,
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    render_effective_prompt,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from homeassistant.components import conversation
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from tests.test_usage_detail_lifecycle import DetailStorage, TotalsStorage


@pytest.mark.parametrize("migrate", [False, True])
@pytest.mark.asyncio
async def test_inferred_azure_connection_accepts_its_underlying_model(
    monkeypatch, migrate
):
    data = LEGACY_STEP_USER_DATA_SCHEMA(
        {
            "api_key": "fabricated",
            "base_url": "https://legacy.openai.azure.com",
            "api_version": "2025-04-01-preview",
            "skip_authentication": True,
        }
    )
    assert data["api_provider"] == "openai"
    options = {
        "chat_model": "my-home-assistant-deployment",
        "api_mode": "chat_completions",
        "max_tokens": 300,
        "functions": "[]",
    }
    subentry = SimpleNamespace(
        subentry_id="agent", subentry_type="conversation", data=options
    )
    entry = SimpleNamespace(
        entry_id="entry",
        data=data,
        version=CONFIG_ENTRY_VERSION,
        disabled_by=None,
        subentries={"agent": subentry},
    )

    def update_entry(target, **changes):
        for key, value in changes.items():
            setattr(target, key, value)

    entries = SimpleNamespace(
        async_entries=lambda _domain: [entry],
        async_update_subentry=MagicMock(),
        async_update_entry=update_entry,
    )
    if migrate:
        await async_migrate_integration(SimpleNamespace(config_entries=entries))
    azure, openai = MagicMock(), MagicMock()
    monkeypatch.setattr(helpers, "AsyncAzureOpenAI", azure)
    monkeypatch.setattr(helpers, "AsyncOpenAI", openai)
    monkeypatch.setattr(helpers, "get_async_client", lambda _hass: object())
    await helpers.get_authenticated_client(
        hass=SimpleNamespace(),
        api_key=data["api_key"],
        base_url=data["base_url"],
        api_version=data["api_version"],
        organization=None,
        api_provider=entry.data.get("api_provider"),
        skip_authentication=True,
    )
    assert azure.called and not openai.called
    options = {**options, "azure_model": "gpt-4.1-mini"}
    control = build_provider_request_snapshot(
        options, {**entry.data, "api_provider": "azure"}, tools_required=True
    )
    assert control.api_kwargs["model"] == "my-home-assistant-deployment"
    # The effective provider must agree with the SDK selected above.
    actual = build_provider_request_snapshot(options, entry.data, tools_required=True)
    assert actual.api_kwargs["model"] == "my-home-assistant-deployment"


class Stream:
    async def __aiter__(self):
        msg = ResponseOutputMessage(
            id="msg_final",
            type="message",
            role="assistant",
            status="completed",
            content=[
                ResponseOutputText(type="output_text", text="Done", annotations=[])
            ],
        )
        for event in [
            SimpleNamespace(
                type="response.output_item.added", output_index=0, item=msg
            ),
            SimpleNamespace(
                type="response.output_text.delta",
                output_index=0,
                content_index=0,
                delta="Done",
            ),
            SimpleNamespace(type="response.output_item.done", output_index=0, item=msg),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(usage=None, status="completed"),
            ),
        ]:
            yield event


@pytest.mark.asyncio
async def test_area_name_swap_produces_identical_provider_request(hass, monkeypatch):
    areas = {
        "opaque_room_a": SimpleNamespace(
            id="opaque_room_a", name="Office", aliases={"Study"}
        ),
        "opaque_room_b": SimpleNamespace(
            id="opaque_room_b", name="Nursery", aliases={"Baby room"}
        ),
    }
    area_registry = SimpleNamespace(
        async_get_area=lambda area_id: areas.get(area_id),
        async_get_area_by_name=lambda value: next(
            (a for a in areas.values() if a.name == value), None
        ),
        async_get_areas_by_alias=lambda value: [
            a for a in areas.values() if value in a.aliases
        ],
    )
    hass.data[ar.DATA_REGISTRY] = area_registry
    hass.data[dr.DATA_REGISTRY] = SimpleNamespace(async_get=lambda value: None)
    hass.data[er.DATA_REGISTRY] = SimpleNamespace(async_get=lambda value: None)
    metadata = {
        "light.node_1": SimpleNamespace(area_id="opaque_room_a", aliases=()),
        "light.node_2": SimpleNamespace(area_id="opaque_room_b", aliases=()),
    }
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.exposed_attributes.get_entity_prompt_metadata",
        lambda _h, entity_id: metadata[entity_id],
    )
    for env in (
        "template.environment",
        "template.environment_limited",
        "template.environment_strict",
    ):
        hass.data[env].globals["extended_openai"] = {
            "working_directory": lambda: "/config/workspace"
        }
    options = agent_config_defaults()
    assert options["local_intents_enabled"] is False
    options["current_datetime_enabled"] = False
    tools = [DEFAULT_CONF_FUNCTION_TOOLS[0]]
    entities = [
        {"entity_id": "light.node_1", "name": "Ceiling", "state": "off", "aliases": []},
        {"entity_id": "light.node_2", "name": "Ceiling", "state": "off", "aliases": []},
    ]
    user = SimpleNamespace(
        text="Turn on the Office lights",
        extra_system_prompt=None,
        satellite_id=None,
        context=SimpleNamespace(user_id="admin"),
        device_id=None,
    )
    create = AsyncMock(side_effect=lambda **kw: Stream())
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(
        data={}, runtime_data=SimpleNamespace(responses=SimpleNamespace(create=create))
    )
    entity.subentry = SimpleNamespace(data=options)
    entity.hass = hass
    entity.entity_id = "conversation.audit"
    entity._usage = None

    async def request():
        prompt = render_effective_prompt(
            hass,
            options,
            exposed_entities=entities,
            current_device_id=None,
            user_input=user,
            skills=[],
        ).text
        log = conversation.ChatLog(hass, "audit")
        log.content[0] = conversation.SystemContent(content=prompt)
        log.async_add_user_content(conversation.UserContent(content=user.text))
        await entity._async_handle_chat_log(log, tools, entities)
        return create.await_args.kwargs

    first = await request()
    context = first["input"][0]["content"]
    for label in ("Office", "Nursery", "Study", "Baby room"):
        assert label in context
    areas["opaque_room_a"].name = "Nursery"
    areas["opaque_room_b"].name = "Office"
    areas["opaque_room_a"].aliases, areas["opaque_room_b"].aliases = (
        areas["opaque_room_b"].aliases,
        areas["opaque_room_a"].aliases,
    )
    second = await request()
    # The same Office request needs different entities after the area-label swap.
    # The provider must receive the changed mapping, not identical context.
    assert first != second


@pytest.mark.parametrize("parent_loaded", [False, True])
@pytest.mark.asyncio
async def test_disabled_conversation_retention_survives_reload_and_reappears(
    hass,
    monkeypatch,
    management_agent,
    management_message,
    parent_loaded,
):
    from unittest.mock import AsyncMock

    from custom_components.extended_openai_conversation_responses import (
        management_ui,
        sensor,
        usage,
    )
    from custom_components.extended_openai_conversation_responses.usage import (
        RequestUsage,
        UsageManager,
    )
    from homeassistant.config_entries import ConfigEntryState

    entry, subentry = management_agent
    entry.state = (
        ConfigEntryState.LOADED if parent_loaded else ConfigEntryState.NOT_LOADED
    )
    manager = UsageManager(
        TotalsStorage(),
        detail_storage=DetailStorage(),
        request_retention_days=30,
        run_retention_days=30,
        agent_subentry_id=subentry.subentry_id,
    )
    await manager.async_initialize()
    async with manager.async_run():
        await manager.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=7)
        )
    if manager._prune_task is not None:
        await manager._prune_task
    hass.data[usage._USAGE_MANAGERS] = {(entry.entry_id, subentry.subentry_id): manager}
    monkeypatch.setattr(usage, "_register_usage_maintenance", lambda *_: None)
    monkeypatch.setattr(
        sensor, "async_get_guest_mode", AsyncMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        management_ui,
        "async_get_request_rules",
        AsyncMock(return_value=SimpleNamespace(snapshot=lambda: {"rules": []})),
    )
    loaded = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message("configuration", "get"),
    )
    saved = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "save",
            revision=loaded["revision"],
            config={"usage_request_retention_days": 0, "usage_run_retention_days": 0},
        ),
    )
    assert saved["valid"] is True
    assert manager._detail_storage.data == {"requests": [], "runs": []}
    # On an actual reload with the conversation entity disabled, HA does not
    # call conversation.async_added_to_hass; the sensor platform still sets up.
    if parent_loaded:
        await sensor.async_setup_entry(hass, entry, MagicMock())
    assert manager.request_retention_days == manager.run_retention_days == 0
    assert manager.recent_runs()["runs"] == []
    disabled_backup = await manager.async_backup_data()
    disabled_disk = manager._detail_storage.data

    enabled = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "save",
            revision=saved["revision"],
            config={"usage_request_retention_days": 30, "usage_run_retention_days": 30},
        ),
    )
    assert enabled["valid"] is True
    if parent_loaded:
        await sensor.async_setup_entry(hass, entry, MagicMock())
    reappeared = manager.recent_runs()["runs"]
    # Disabled must erase durable details, so a later policy edit cannot revive them.
    assert (
        len(disabled_backup["requests"]),
        len(disabled_backup["runs"]),
        len(disabled_disk["requests"]),
        len(disabled_disk["runs"]),
        len(reappeared),
    ) == (0, 0, 0, 0, 0)


async def test_retention_failure_reports_committed_configuration_and_can_retry(
    hass,
    monkeypatch,
    management_agent,
    management_message,
):
    from custom_components.extended_openai_conversation_responses import (
        management_ui,
        usage,
    )
    from custom_components.extended_openai_conversation_responses.mutation_outcomes import (
        mutation_failure,
    )
    from homeassistant.exceptions import HomeAssistantError

    entry, subentry = management_agent
    manager = usage.UsageManager(TotalsStorage(), detail_storage=DetailStorage())
    await manager.async_initialize()
    hass.data[usage._USAGE_MANAGERS] = {(entry.entry_id, subentry.subentry_id): manager}
    monkeypatch.setattr(usage, "_register_usage_maintenance", lambda *_: None)
    monkeypatch.setattr(
        management_ui,
        "async_get_request_rules",
        AsyncMock(return_value=SimpleNamespace(snapshot=lambda: {"rules": []})),
    )
    loaded = await management_ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    prune = manager.async_prune_details
    failing = AsyncMock(side_effect=OSError("test store unavailable"))
    monkeypatch.setattr(manager, "async_prune_details", failing)
    with pytest.raises(HomeAssistantError, match="Configuration saved") as error:
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "configuration",
                "save",
                revision=loaded["revision"],
                config={"usage_request_retention_days": 0},
            ),
        )
    assert mutation_failure(error.value).outcome == "committed"
    assert subentry.data["usage_request_retention_days"] == 0
    monkeypatch.setattr(manager, "async_prune_details", prune)
    current = await management_ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    spy = AsyncMock(wraps=prune)
    monkeypatch.setattr(manager, "async_prune_details", spy)
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "save",
            revision=current["revision"],
            config={"usage_request_retention_days": 0},
        ),
    )
    assert result["valid"] is True
    spy.assert_awaited_once()


async def test_new_hostname_inferred_azure_setup_saves_canonical_provider(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import config_flow

    monkeypatch.setattr(config_flow, "get_authenticated_client", AsyncMock())
    data = {
        "api_key": "fabricated",
        "api_provider": "openai",
        "base_url": "https://new.openai.azure.com",
        "api_version": "2025-04-01-preview",
    }
    await config_flow.validate_input(hass, data)
    assert data["api_provider"] == "azure"
    assert (
        config_flow.get_authenticated_client.await_args.kwargs["api_provider"]
        == "azure"
    )
