"""Tests for Voice Identity runtime source-device normalization."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    voice_identity_runtime,
)
from custom_components.extended_openai_conversation_responses import conversation


def test_registry_device_temporarily_overrides_satellite_source() -> None:
    user_input = SimpleNamespace(
        device_id="device-registry-id",
        satellite_id="assist_satellite.kitchen",
    )

    with voice_identity_runtime._prefer_registry_device_source(user_input):
        assert user_input.device_id == "device-registry-id"
        assert user_input.satellite_id is None

    assert user_input.satellite_id == "assist_satellite.kitchen"


def test_satellite_fallback_is_preserved_without_registry_device() -> None:
    user_input = SimpleNamespace(
        device_id=None,
        satellite_id="assist_satellite.kitchen",
    )

    with voice_identity_runtime._prefer_registry_device_source(user_input):
        assert user_input.satellite_id == "assist_satellite.kitchen"


@pytest.mark.asyncio
async def test_installed_wrapper_uses_registry_device_and_restores_satellite(
    monkeypatch,
) -> None:
    observed: list[tuple[str | None, str | None]] = []

    async def original_process(_self, user_input):
        observed.append((user_input.device_id, user_input.satellite_id))
        return "processed"

    monkeypatch.setattr(
        conversation.ExtendedOpenAIAgentEntity,
        "_async_process",
        original_process,
    )
    monkeypatch.setattr(voice_identity_runtime, "_INSTALLED", False)

    voice_identity_runtime.install_voice_identity_runtime()

    user_input = SimpleNamespace(
        device_id="device-registry-id",
        satellite_id="assist_satellite.kitchen",
    )
    result = await conversation.ExtendedOpenAIAgentEntity._async_process(
        object(), user_input
    )

    assert result == "processed"
    assert observed == [("device-registry-id", None)]
    assert user_input.satellite_id == "assist_satellite.kitchen"
