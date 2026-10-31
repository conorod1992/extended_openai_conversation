"""Read retries use actual I/O, without granting overrides an effect exemption."""

from functools import wraps
import sqlite3
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.functions.sqlite import (
    SqliteFunction,
)
from custom_components.extended_openai_conversation_responses.functions.template import (
    TemplateFunction,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    clear_unacknowledged_calls,
    remember_unacknowledged_calls,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_tool_error_recovery import _chat_log
from tests.test_receipt_ownership_contracts import dispatch


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "states.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE states (state TEXT)")
        db.execute("INSERT INTO states VALUES ('22.5')")
    return path


async def sqlite_retry_witness(hass, database):
    reads = []

    async def run(function, *args):
        result = function(*args)
        reads.append(result)
        return result

    hass.async_add_executor_job.side_effect = run
    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    config = {
        "type": "sqlite",
        "db_url": str(database),
        "query": "SELECT state FROM states",
    }
    backend = SqliteFunction()
    assert await dispatch(
        entity, log, backend, config, name="read", call_id="first"
    ) == [{"state": "22.5"}]
    # Failed provider continuations retain through this production boundary.
    remember_unacknowledged_calls(entity, log, set())
    with sqlite3.connect(database) as db:
        db.execute("UPDATE states SET state='23.0'")
    assert await dispatch(
        entity, log, backend, config, name="read", call_id="fresh-id"
    ) == [{"state": "23.0"}]
    assert len(reads) == 2
    clear_unacknowledged_calls(entity, log.conversation_id)
    await dispatch(entity, log, backend, config, name="read", call_id="acknowledged")
    assert len(reads) == 3


async def test_sqlite_read_retries_after_failed_continuation(hass, database):
    await sqlite_retry_witness(hass, database)


async def test_read_witness_detects_opaque_sqlite_receipt(hass, database, monkeypatch):
    monkeypatch.delattr(SqliteFunction.execute, "_eoai_owns_execution_receipts")
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await sqlite_retry_witness(hass, database)


async def test_template_retry_has_no_effect_receipt(hass):
    backend = TemplateFunction()
    config = backend.validate_schema(
        {"type": "template", "value_template": "{{ 2 + 2 }}"}
    )
    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    for call_id in ["first", "fresh-id"]:
        assert (
            await dispatch(entity, log, backend, config, name="read", call_id=call_id)
            == "4"
        )
        remember_unacknowledged_calls(entity, log, set())


@pytest.mark.parametrize("base", [SqliteFunction, TemplateFunction])
async def test_overridden_read_backend_keeps_conservative_protection(hass, base):
    effects = []

    class Override(base):
        @wraps(base.execute)
        async def execute(self, *args):
            effects.append("effect")
            return "ok"

    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    backend, config = Override(), {"type": "opaque"}
    await dispatch(entity, log, backend, config, name="opaque", call_id="first")
    remember_unacknowledged_calls(entity, log, set())
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await dispatch(entity, log, backend, config, name="opaque", call_id="retry")
    assert effects == ["effect"]


@pytest.mark.parametrize("backend", ["sqlite", "template"])
async def test_scheduled_read_still_creates_only_one_durable_job(hass, backend):
    from copy import deepcopy

    from custom_components.extended_openai_conversation_responses import delayed_tools
    from custom_components.extended_openai_conversation_responses.const import DOMAIN
    from custom_components.extended_openai_conversation_responses.function_call_budget import (
        FunctionCallBudget,
    )
    from custom_components.extended_openai_conversation_responses.tool_exchange import (
        async_execute_tool_exchange,
    )
    from tests.test_function_tool_error_recovery import _call, _retain_calls
    from tests.test_review_replay_execution import _entity, _execute, _tool

    manager = delayed_tools.DelayedToolManager(hass)
    manager._setup_complete = True
    persisted = {"calls": []}

    async def save(data):
        persisted.update(deepcopy(data))

    manager._store = SimpleNamespace(async_save=save)
    hass.data.setdefault(DOMAIN, {})[delayed_tools.DATA_DELAYED_TOOL_MANAGER] = manager
    config = (
        {"type": "sqlite", "query": "SELECT 1"}
        if backend == "sqlite"
        else {"type": "template", "value_template": "{{ 2 + 2 }}"}
    )
    entity, log, tool = _entity(hass), _chat_log(hass), _tool(config)
    call = _call("first", "effect", {"delay": {"seconds": 30}})
    _retain_calls(log, [call])
    await _execute(entity, tool, call)
    assert len(persisted["calls"]) == 1
    remember_unacknowledged_calls(entity, log, set())

    async def execute(definition, call, *_):
        return await _execute(entity, definition, call)

    entity._execute_function_tool = execute
    retry = _call("fresh", "effect", call.tool_args)
    _retain_calls(log, [retry])
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await async_execute_tool_exchange(
            entity, log, [retry], [tool], FunctionCallBudget(10), None, []
        )
    assert len(persisted["calls"]) == 1


async def test_template_child_does_not_exempt_composite_mutation(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses.function_call_budget import (
        FunctionCallBudget,
    )
    from custom_components.extended_openai_conversation_responses.tool_exchange import (
        async_execute_tool_exchange,
    )
    from tests.test_function_tool_error_recovery import _call, _retain_calls
    from tests.test_review_replay_execution import (
        ALIAS,
        NATIVE,
        _entity,
        _execute,
        _install_native_boundary,
        _tool,
    )

    _install_native_boundary(hass, monkeypatch)
    tool = _tool(
        {
            "type": "composite",
            "sequence": [{"type": "template", "value_template": "{{ 2 + 2 }}"}, NATIVE],
        }
    )
    entity, log = _entity(hass), _chat_log(hass)
    first = _call("first", "effect", ALIAS)
    _retain_calls(log, [first])
    await _execute(entity, tool, first)
    assert hass.services.async_call.await_count == 1
    remember_unacknowledged_calls(entity, log, set())

    async def execute(definition, call, *_):
        return await _execute(entity, definition, call)

    entity._execute_function_tool = execute
    retry = _call("fresh", "effect", ALIAS)
    _retain_calls(log, [retry])
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await async_execute_tool_exchange(
            entity, log, [retry], [tool], FunctionCallBudget(10), None, []
        )
    assert hass.services.async_call.await_count == 1
