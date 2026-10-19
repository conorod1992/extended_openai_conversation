"""Public export, quarantine and nested-entry regressions from the follow-up audit."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers import translation

from custom_components.extended_openai_conversation_responses import (
    conversation as owner,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN


@pytest.mark.parametrize("language", ["en", "de"])
async def test_retired_model_text_loads_through_ha_translation_cache(
    hass, monkeypatch, language
):
    root = Path(owner.__file__).parent
    integration = SimpleNamespace(file_path=root, has_translations=True, name="EOAI")
    monkeypatch.setattr(
        translation,
        "async_get_integrations",
        AsyncMock(return_value={DOMAIN: integration}),
    )
    cache = translation._TranslationCache(hass)
    assert await cache.async_fetch(language, "config", {DOMAIN}), (
        "control: runtime config translations load"
    )
    issues = await cache.async_fetch(language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.retired_model."
    assert issues[prefix + "title"]
    description = issues[prefix + "description"]
    assert all(
        "{" + name + "}" in description
        for name in ("assistant", "model", "shutdown_at")
    )
