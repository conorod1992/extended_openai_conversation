"""Template cleanup through HA's actual one-shot event bus."""

from custom_components.extended_openai_conversation_responses.template import (
    ExtendedOpenAITemplateManager,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers.template import TemplateEnvironment


async def test_template_stop_listener_cleanup_and_reload(hass, caplog):
    original = TemplateEnvironment.__init__
    for _ in range(2):
        manager = ExtendedOpenAITemplateManager(hass)
        await manager.async_setup()
        manager.acquire("test-entry")
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
        await manager.async_on_unload()
        assert manager._remove_stop_listener is None
        assert not manager.in_use
        assert TemplateEnvironment.__init__ is original
    assert "Unable to remove unknown job listener" not in caplog.text


async def test_template_unload_before_stop_is_idempotent(hass, caplog):
    manager = ExtendedOpenAITemplateManager(hass)
    await manager.async_setup()
    await manager.async_on_unload()
    await manager.async_on_unload()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert "Unable to remove unknown job listener" not in caplog.text
