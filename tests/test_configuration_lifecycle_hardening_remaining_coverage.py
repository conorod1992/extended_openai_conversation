"""Residual coverage for configuration lifecycle hardening fallbacks and guards."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    configuration_lifecycle_hardening as hardening,
    const,
    conversation,
    conversation_archive,
    temporary_memory,
)


def _entity(options: dict) -> SimpleNamespace:
    statuses: list[tuple[str, bool, Exception | None, bool]] = []

    def set_status(
        subsystem: str,
        configured: bool,
        error: Exception | None = None,
        *,
        healthy: bool = False,
    ) -> None:
        statuses.append((subsystem, configured, error, healthy))

    return SimpleNamespace(
        hass=object(),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent", data=options),
        _memory=None,
        _temporary_memory=None,
        _archive=None,
        _knowledge=None,
        _usage=None,
        _set_subsystem_status=set_status,
        _async_create_embeddings=AsyncMock(),
        statuses=statuses,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.5", "12.5"),
        ("abc", "abc"),
        (12.0, 12),
    ],
)
def test_timeout_coercion_handles_invalid_strings_and_integral_float(
    monkeypatch, raw, expected
) -> None:
    monkeypatch.setattr(
        const,
        "CONVERSATION_TIMEOUT_OPTIONS",
        const.CONVERSATION_TIMEOUT_OPTIONS,
    )
    monkeypatch.setattr(
        agent_config,
        "CONVERSATION_TIMEOUT_OPTIONS",
        agent_config.CONVERSATION_TIMEOUT_OPTIONS,
    )
    monkeypatch.setattr(agent_config, "_coerce_legacy_numbers", lambda _config: None)

    hardening._install_conversation_timeout_validation()

    config = {const.CONF_CONVERSATION_TIMEOUT_MINUTES: raw}
    agent_config._coerce_legacy_numbers(config)

    assert config[const.CONF_CONVERSATION_TIMEOUT_MINUTES] == expected


@pytest.mark.parametrize("hass_value", [None, object()])
def test_set_subsystem_status_noops_without_callable_setter(hass_value) -> None:
    sentinel = object()
    entity = SimpleNamespace(hass=hass_value, _set_subsystem_status=sentinel)

    hardening._set_subsystem_status(entity, "archive", True)

    assert entity._set_subsystem_status is sentinel


@pytest.mark.asyncio
async def test_runtime_refresh_returns_when_configuration_identity_is_unchanged(
    monkeypatch,
) -> None:
    options: dict = {}
    entity = _entity(options)
    setattr(entity, hardening._RUNTIME_CONFIG_DATA, options)
    setattr(entity, hardening._RUNTIME_CONFIG_RETRY, False)

    monkeypatch.setattr(
        hardening,
        "_gate_disabled_subsystems",
        lambda *_args: pytest.fail("outer identity fast path should return first"),
    )

    await hardening.async_reconcile_runtime_configuration(entity)


@pytest.mark.asyncio
async def test_temporary_memory_initialization_failure_requests_retry(
    monkeypatch, caplog
) -> None:
    options = {
        const.CONF_TEMPORARY_MEMORY: "enabled",
        const.CONF_ARCHIVE_ENABLED: False,
        const.CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
        const.CONF_KNOWLEDGE_ENABLED: False,
    }
    entity = _entity(options)
    failure = RuntimeError("temporary store unavailable")
    get_temporary = AsyncMock(side_effect=failure)
    monkeypatch.setattr(temporary_memory, "async_get_temporary_memory", get_temporary)
    monkeypatch.setattr(hardening, "memory_enabled", lambda _options: False)

    await hardening.async_reconcile_runtime_configuration(entity, force=True)

    assert entity._temporary_memory is None
    assert getattr(entity, hardening._RUNTIME_CONFIG_RETRY) is True
    assert any(
        subsystem == "temporary_memory" and configured and error is failure
        for subsystem, configured, error, _healthy in entity.statuses
    )
    assert "Unable to initialize temporary memory" in caplog.text
    get_temporary.assert_awaited_once_with(entity.hass, "entry", "agent")


@pytest.mark.asyncio
async def test_archive_get_failure_requests_retry_and_records_status(
    monkeypatch, caplog
) -> None:
    options = {
        const.CONF_TEMPORARY_MEMORY: const.TEMPORARY_MEMORY_OFF,
        const.CONF_ARCHIVE_ENABLED: True,
        const.CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
        const.CONF_KNOWLEDGE_ENABLED: False,
    }
    entity = _entity(options)
    failure = RuntimeError("archive unavailable")
    get_archive = AsyncMock(side_effect=failure)
    monkeypatch.setattr(conversation_archive, "async_get_archive", get_archive)
    monkeypatch.setattr(hardening, "memory_enabled", lambda _options: False)

    await hardening.async_reconcile_runtime_configuration(entity, force=True)

    assert entity._archive is None
    assert getattr(entity, hardening._RUNTIME_CONFIG_RETRY) is True
    assert any(
        subsystem == "archive" and configured and error is failure
        for subsystem, configured, error, _healthy in entity.statuses
    )
    assert "Unable to initialize conversation archive" in caplog.text
    get_archive.assert_awaited_once_with(entity.hass, "entry", "agent")


@pytest.mark.asyncio
async def test_archive_initializer_leaving_runtime_missing_requests_retry(
    monkeypatch,
) -> None:
    options = {
        const.CONF_TEMPORARY_MEMORY: const.TEMPORARY_MEMORY_OFF,
        const.CONF_ARCHIVE_ENABLED: True,
        const.CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
        const.CONF_KNOWLEDGE_ENABLED: False,
    }
    entity = _entity(options)
    initializer = AsyncMock()
    entity._async_initialize_archive = initializer
    monkeypatch.setattr(hardening, "memory_enabled", lambda _options: False)

    await hardening.async_reconcile_runtime_configuration(entity, force=True)

    initializer.assert_awaited_once_with(True)
    assert entity._archive is None
    assert getattr(entity, hardening._RUNTIME_CONFIG_RETRY) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "options", "message"),
    [
        (
            "_async_execute_memory_tool",
            {},
            "persistent memory is disabled",
        ),
        (
            "_async_execute_temporary_memory_tool",
            {const.CONF_TEMPORARY_MEMORY: const.TEMPORARY_MEMORY_OFF},
            "temporary memory is disabled",
        ),
        (
            "_async_execute_archive_tool",
            {
                const.CONF_ARCHIVE_ENABLED: False,
                const.CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
            },
            "conversation archive is disabled",
        ),
    ],
)
async def test_disabled_tool_guards_block_original_execution(
    monkeypatch, tool_name: str, options: dict, message: str
) -> None:
    cls = conversation.ExtendedOpenAIAgentEntity
    patched_names = (
        "_async_process",
        "_async_retrieve_memories",
        "_async_retrieve_temporary_memories",
        "_async_execute_memory_tool",
        "_async_execute_temporary_memory_tool",
        "_async_execute_archive_tool",
        "supports_streaming",
    )
    for name in patched_names:
        monkeypatch.setattr(cls, name, getattr(cls, name))

    originals = {
        "_async_execute_memory_tool": AsyncMock(),
        "_async_execute_temporary_memory_tool": AsyncMock(),
        "_async_execute_archive_tool": AsyncMock(),
    }
    for name, original in originals.items():
        monkeypatch.setattr(cls, name, original)

    monkeypatch.setattr(hardening, "memory_enabled", lambda _options: False)
    hardening._install_runtime_configuration_lifecycle()

    entity = SimpleNamespace(subentry=SimpleNamespace(data=options))
    wrapped = getattr(cls, tool_name)

    with pytest.raises(RuntimeError, match=message):
        await wrapped(entity)

    originals[tool_name].assert_not_awaited()
