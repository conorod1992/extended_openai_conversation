"""Template ownership through the production startup and entry lifecycle."""

from asyncio import CancelledError
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    management_loading_performance,
    template,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers.template import Template, TemplateEnvironment


@pytest.fixture
async def lifecycle(hass, monkeypatch):
    """Run real installers; isolate network, services and platform forwarding only."""
    original = TemplateEnvironment.__init__
    monkeypatch.setattr(TemplateEnvironment, "__init__", original)
    for name in (
        "async_migrate_integration",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
        "async_setup_debug_ui",
        "async_setup_management_ui",
        "async_setup_delayed_tools",
        "get_authenticated_client",
    ):
        monkeypatch.setattr(integration, name, AsyncMock())
    monkeypatch.setattr(
        integration, "setup_provider_credentials_websocket", MagicMock()
    )
    monkeypatch.setattr(template, "async_setup_delayed_tools", AsyncMock())
    for name in ("async_setup_cached_debug_ui", "async_setup_cached_management_ui"):
        monkeypatch.setattr(management_loading_performance, name, AsyncMock())
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    assert await integration.async_setup(hass, {})
    assert await integration.async_setup(hass, {})
    assert TemplateEnvironment.__init__ is original
    yield original
    manager = hass.data.get(DOMAIN, {}).get(template.DATA_TEMPLATE_MANAGER)
    if manager is not None:
        await manager.async_on_unload()


def entry(entry_id):
    return SimpleNamespace(
        entry_id=entry_id,
        data={"api_key": "test"},
        async_on_unload=MagicMock(),
        add_update_listener=MagicMock(),
    )


async def test_entries_agents_reload_and_final_unload(hass, lifecycle):
    first, second = entry("first"), entry("second")
    cached_environments = {
        "template.environment": hass.data["template.environment"],
        "template.environment_limited": TemplateEnvironment(hass, limited=True),
        "template.environment_strict": TemplateEnvironment(hass, strict=True),
    }
    for key, environment in cached_environments.items():
        hass.data[key] = environment
    prior = {key: {"prior": object()} for key in cached_environments}
    for key, environment in cached_environments.items():
        environment.globals[template.TEMPLATE_EXTENDED_OPENAI] = prior[key]

    assert await integration.async_setup_entry(hass, first)
    replacement = TemplateEnvironment.__init__
    manager = hass.data[DOMAIN][template.DATA_TEMPLATE_MANAGER]
    await manager.async_setup()
    assert await integration.async_setup_entry(hass, first)
    assert await integration.async_setup_entry(hass, second)
    assert TemplateEnvironment.__init__ is replacement
    for environment in cached_environments.values():
        assert (
            environment.globals[template.TEMPLATE_EXTENDED_OPENAI]
            is manager._extended_openai
        )

    environments = [TemplateEnvironment(hass), TemplateEnvironment(hass, strict=True)]
    for environment in environments:
        assert (
            environment.globals[template.TEMPLATE_EXTENDED_OPENAI]
            is manager._extended_openai
        )
    assert Template(
        "{{ extended_openai.working_directory() }}", hass
    ).async_render() == str(
        template.Path(hass.config.config_dir) / template.DEFAULT_WORKING_DIRECTORY
    )
    hass.config_entries.async_unload_platforms.return_value = False
    assert not await integration.async_unload_entry(hass, first)
    assert manager._entry_ids == {"first", "second"}
    hass.config_entries.async_unload_platforms.return_value = True
    assert await integration.async_unload_entry(hass, first)
    assert TemplateEnvironment.__init__ is replacement
    assert await integration.async_setup_entry(hass, first)
    assert await integration.async_unload_entry(hass, second)
    assert TemplateEnvironment.__init__ is replacement
    assert await integration.async_unload_entry(hass, first)
    assert TemplateEnvironment.__init__ is lifecycle
    for key, environment in cached_environments.items():
        assert environment.globals[template.TEMPLATE_EXTENDED_OPENAI] is prior[key]
    assert all(template.TEMPLATE_EXTENDED_OPENAI not in e.globals for e in environments)
    assert await integration.async_unload_entry(hass, first)
    assert await integration.async_setup_entry(hass, second)
    assert await integration.async_unload_entry(hass, second)
    assert TemplateEnvironment.__init__ is lifecycle


@pytest.mark.parametrize("delegates", [False, True])
async def test_later_replacement_survives_unload_and_reload(
    hass, lifecycle, monkeypatch, delegates
):
    consumer = entry("consumer")
    await integration.async_setup_entry(hass, consumer)
    installed = TemplateEnvironment.__init__
    environment = TemplateEnvironment(hass)
    newer_global = object()
    environment.globals[template.TEMPLATE_EXTENDED_OPENAI] = newer_global

    def newer(self, *args, **kwargs):
        (installed if delegates else lifecycle)(self, *args, **kwargs)
        self.globals["newer"] = True

    monkeypatch.setattr(TemplateEnvironment, "__init__", newer)
    # Repeated acquisition must not overwrite a patch installed after ours.
    await integration.async_setup_entry(hass, consumer)
    assert TemplateEnvironment.__init__ is newer
    await integration.async_unload_entry(hass, consumer)
    assert TemplateEnvironment.__init__ is newer
    assert environment.globals[template.TEMPLATE_EXTENDED_OPENAI] is newer_global
    fresh = TemplateEnvironment(hass)
    assert fresh.globals["newer"] is True
    assert template.TEMPLATE_EXTENDED_OPENAI not in fresh.globals
    await integration.async_setup_entry(hass, consumer)
    assert template.TEMPLATE_EXTENDED_OPENAI in TemplateEnvironment(hass).globals
    await integration.async_unload_entry(hass, consumer)
    assert TemplateEnvironment.__init__ is newer
    assert template.TEMPLATE_EXTENDED_OPENAI not in TemplateEnvironment(hass).globals


async def test_existing_foreign_wrapper_is_restored(hass, lifecycle, monkeypatch):
    """A constructor already owned by another component remains the delegate."""

    def previous(self, *args, **kwargs):
        lifecycle(self, *args, **kwargs)
        self.globals["previous"] = True

    monkeypatch.setattr(TemplateEnvironment, "__init__", previous)
    consumer = entry("consumer")
    await integration.async_setup_entry(hass, consumer)
    assert TemplateEnvironment.__init__ is not previous
    assert TemplateEnvironment(hass).globals["previous"] is True
    await integration.async_unload_entry(hass, consumer)
    assert TemplateEnvironment.__init__ is previous
    assert TemplateEnvironment(hass).globals["previous"] is True


@pytest.mark.parametrize("failure", [RuntimeError, CancelledError])
async def test_partial_install_rollback_and_retry(hass, lifecycle, failure):
    consumer = entry("failed")
    hass.bus.async_listen_once.side_effect = failure("setup interrupted")
    with pytest.raises(failure, match="setup interrupted"):
        await integration.async_setup_entry(hass, consumer)
    assert TemplateEnvironment.__init__ is lifecycle
    assert template.DATA_TEMPLATE_MANAGER not in hass.data[DOMAIN]
    assert (
        template.TEMPLATE_EXTENDED_OPENAI
        not in hass.data["template.environment"].globals
    )
    hass.config_entries.async_unload_platforms.assert_awaited_once()
    hass.bus.async_listen_once.side_effect = None
    await integration.async_setup_entry(hass, consumer)
    await integration.async_unload_entry(hass, consumer)
    assert TemplateEnvironment.__init__ is lifecycle


async def test_setup_rollback_keeps_templates_until_platforms_unload(hass, lifecycle):
    """Forwarded platforms keep template globals until their rollback finishes."""
    consumer = entry("failed")
    consumer.add_update_listener.side_effect = RuntimeError("listener failed")

    async def unload_platforms(_entry, _platforms):
        manager = hass.data[DOMAIN][template.DATA_TEMPLATE_MANAGER]
        assert TemplateEnvironment.__init__ is manager._replacement_init
        assert (
            hass.data["template.environment"].globals[template.TEMPLATE_EXTENDED_OPENAI]
            is manager._extended_openai
        )
        return True

    hass.config_entries.async_unload_platforms = AsyncMock(side_effect=unload_platforms)
    with pytest.raises(RuntimeError, match="listener failed"):
        await integration.async_setup_entry(hass, consumer)
    assert TemplateEnvironment.__init__ is lifecycle
    assert template.DATA_TEMPLATE_MANAGER not in hass.data[DOMAIN]


async def test_failure_after_acquisition_preserves_other_consumer(hass, lifecycle):
    first, failed = entry("first"), entry("failed")
    await integration.async_setup_entry(hass, first)
    replacement = TemplateEnvironment.__init__
    failed.add_update_listener.side_effect = RuntimeError("listener failed")
    with pytest.raises(RuntimeError, match="listener failed"):
        await integration.async_setup_entry(hass, failed)
    assert TemplateEnvironment.__init__ is replacement
    assert hass.data[DOMAIN][template.DATA_TEMPLATE_MANAGER]._entry_ids == {"first"}
    await integration.async_unload_entry(hass, first)
    assert TemplateEnvironment.__init__ is lifecycle


async def test_stop_releases_patch_and_restart_can_acquire(hass, lifecycle):
    consumer = entry("consumer")
    await integration.async_setup_entry(hass, consumer)
    stop_event, stop = hass.bus.async_listen_once.call_args.args
    assert stop_event == EVENT_HOMEASSISTANT_STOP
    await stop(None)
    assert TemplateEnvironment.__init__ is lifecycle
    assert template.DATA_TEMPLATE_MANAGER not in hass.data[DOMAIN]
    await integration.async_setup_entry(hass, consumer)
    replacement = TemplateEnvironment.__init__
    await stop(None)  # A stale callback cannot tear down the new manager.
    assert TemplateEnvironment.__init__ is replacement
    await integration.async_unload_entry(hass, consumer)
    assert TemplateEnvironment.__init__ is lifecycle
