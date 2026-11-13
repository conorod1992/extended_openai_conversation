"""Template constructor forwarding supports current and future HA arguments."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import template
from tests.test_template_coverage import _hass


@pytest.mark.parametrize("keyword_hass", [False, True])
async def test_additional_constructor_arguments_are_forwarded_exactly(
    monkeypatch, keyword_hass
):
    calls = []

    class FutureEnvironment:
        def __init__(self, *args, **kwargs):
            calls.append((args, dict(kwargs)))
            self.globals = {"other_integration": True}

    monkeypatch.setattr(template, "TemplateEnvironment", FutureEnvironment)
    hass = _hass()
    manager = template.ExtendedOpenAITemplateManager(hass)
    original = FutureEnvironment.__init__
    await manager.async_setup()
    sentinel = object()
    try:
        args = () if keyword_hass else (hass, True, False, None, sentinel)
        kwargs = (
            {"hass": hass, "limited": True, "extra": sentinel}
            if keyword_hass
            else {"future_flag": sentinel}
        )
        environment = FutureEnvironment(*args, **kwargs)
        assert calls[-1] == (args, kwargs)
        assert (
            environment.globals[template.TEMPLATE_EXTENDED_OPENAI]
            is manager._extended_openai
        )
        assert environment.globals["other_integration"] is True
        foreign = FutureEnvironment(SimpleNamespace(data={}), extra=sentinel)
        assert template.TEMPLATE_EXTENDED_OPENAI not in foreign.globals
        no_hass = FutureEnvironment(extra=sentinel)
        assert template.TEMPLATE_EXTENDED_OPENAI not in no_hass.globals
    finally:
        await manager.async_on_unload()
    assert FutureEnvironment.__init__ is original
    assert template.TEMPLATE_EXTENDED_OPENAI not in environment.globals
    assert environment.globals["other_integration"] is True


@pytest.mark.parametrize("keyword_hass", [False, True])
async def test_current_ha_constructor_preserves_ownership_and_unloading(
    hass, monkeypatch, keyword_hass
):
    original = template.TemplateEnvironment.__init__
    monkeypatch.setattr(template.TemplateEnvironment, "__init__", original)
    manager = template.ExtendedOpenAITemplateManager(hass)
    await manager.async_setup()
    try:
        environment = (
            template.TemplateEnvironment(hass=hass, limited=True)
            if keyword_hass
            else template.TemplateEnvironment(hass, True, False)
        )
        assert (
            environment.globals[template.TEMPLATE_EXTENDED_OPENAI]
            is manager._extended_openai
        )
        independent = template.TemplateEnvironment(None)
        assert template.TEMPLATE_EXTENDED_OPENAI not in independent.globals
    finally:
        await manager.async_on_unload()
    assert template.TemplateEnvironment.__init__ is original
    assert template.TEMPLATE_EXTENDED_OPENAI not in environment.globals
