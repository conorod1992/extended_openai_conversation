"""Regression checks for provider, storage, and repair boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import orjson
import pytest

from custom_components.extended_openai_conversation_responses import (
    debug,
    delayed_tools,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_model_repair_snapshot,
    validate_agent_sampling_values,
)
from custom_components.extended_openai_conversation_responses.entity import (
    _make_schema_nullable,
    _normalize_function_result,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.usage import extract_usage
from homeassistant.exceptions import HomeAssistantError
from tests.test_knowledge import FakeStorage
from tests.test_quiet_hours_transition_specification import MemoryTransitionDriver
from tests.test_restore_recovery_validation import _journal


async def test_repair_snapshot_preserves_legacy_values_without_saving():
    raw = {
        "max_tokens": None,
        "max_function_calls_per_conversation": -1,
        "temperature": 3,
        "speech_regex_replacements": [{"pattern": "[", "replacement": ""}],
    }
    snapshot = agent_config_model_repair_snapshot(
        {
            **raw,
            "functions": [
                {
                    "spec": {"name": "who"},
                    "function": {"type": "native", "name": "get_user_from_user_id"},
                }
            ],
        }
    )
    assert all(snapshot[key] == value for key, value in raw.items())
    assert raw["max_tokens"] is None
    with pytest.raises(AgentConfigError, match="temperature"):
        validate_agent_sampling_values({"temperature": float("nan")})


def test_schema_reference_does_not_constrain_null_alternative():
    schema = {"type": ["string", "null"], "$ref": "#/$defs/Note"}
    _make_schema_nullable(schema)
    assert schema == {
        "anyOf": [
            {"type": ["string", "null"], "$ref": "#/$defs/Note"},
            {"type": "null"},
        ]
    }


def test_provider_capture_and_tool_results_keep_serializable_data():
    value = {"\ud800": "broken\udfff", r"\ud800": "valid", "large": 2**80}
    raw_result = _normalize_function_result(value)
    assert raw_result[r"\ud800"] == "valid"
    assert len(raw_result) == 3
    orjson.dumps(raw_result)
    orjson.dumps(debug._jsonable(value))
    usage = {"input_tokens_details": {"cached_tokens": 2, "\ud800": 9}}
    assert extract_usage(usage).details == {"input_cached_tokens": 2}
    assert (
        debug._extract_usage(SimpleNamespace(usage={"total_tokens": 2**80}))[
            "total_tokens"
        ]
        == 0
    )


async def test_deleted_knowledge_creation_receipt_survives_reload():
    storage = FakeStorage()
    library = KnowledgeLibrary(storage)
    await library.async_initialize()
    token = "9" * 32
    source = await library.async_create("Title", "", "Content", create_token=token)
    await library.async_delete(source.source_id)

    library = KnowledgeLibrary(storage)
    await library.async_initialize()
    retried = await library.async_create("Title", "", "Content", create_token=token)
    assert retried.source_id == source.source_id
    assert library.source_count == 0


async def test_committed_journal_ignores_obsolete_invalid_configuration():
    journal = _journal()
    journal["phase"] = "committed"
    journal["rollback"]["agent"]["config"]["temperature"] = "invalid"
    _, target, _ = await restore_recovery._async_load_journal(
        journal, "entry-1", "agent-new"
    )
    assert target.config["prompt"] == "new configuration"


async def test_daytime_reconciliation_retains_delayed_quiet_application(
    hass, monkeypatch
):
    driver = MemoryTransitionDriver(hass, monkeypatch, "volume", deferred=True)
    driver.hold_feedback = True
    await driver.reconcile(0)
    driver.report(driver.values["baseline"])
    await driver.reconcile(10)
    assert [command[-1] for command in driver.commands] == [driver.values["quiet"]]

    driver.hold_feedback = False
    driver.feedback()
    assert driver.current() == driver.values["quiet"]
    await driver.reconcile(10.1)
    driver.feedback()
    assert driver.current() == driver.values["baseline"]


async def test_native_schedule_must_pass_admission_before_queueing(hass, monkeypatch):
    from tests.test_delayed_tools import _entity, _valid_tool

    authorize = AsyncMock(side_effect=HomeAssistantError("Native schedule rejected"))
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.native.NativeFunction.async_authorize_scheduled_services",
        authorize,
    )
    manager = delayed_tools.DelayedToolManager(hass)
    manager._setup_complete = manager._started = True
    manager._store = SimpleNamespace(async_save=AsyncMock())
    manager._arm = Mock()

    with pytest.raises(HomeAssistantError, match="Native schedule rejected"):
        await manager.async_schedule(
            _entity(hass),
            "control_light",
            {"domain": "light", "service": "turn_on"},
            None,
            function_tool=_valid_tool(),
            execution_delay={"seconds": 10},
        )
    authorize.assert_awaited_once()
    manager._store.async_save.assert_not_awaited()
    manager._arm.assert_not_called()
    assert not manager._records
