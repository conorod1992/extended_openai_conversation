"""Observe the production category application contract and its sensitivity."""

from copy import deepcopy
import inspect
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import restore_recovery
from custom_components.extended_openai_conversation_responses.const import (
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
)
from tests.restore_participants import participants
from tests.test_backup import _prepared


async def _assert_application_contract(apply, active) -> None:
    """Witness ordered side effects, exact data and retention at write time."""
    prepared = _prepared()
    prepared.config[CONF_USAGE_REQUEST_RETENTION_DAYS] = 11
    prepared.config[CONF_USAGE_RUN_RETENTION_DAYS] = 22
    prepared.archive_active = active
    writes = []
    state = {}

    def manager(name):
        async def replace(*args, **kwargs):
            writes.append(name)
            assert managers.usage.request_retention_days == 11
            assert managers.usage.run_retention_days == 22
            state[name] = deepcopy((args, kwargs))

        return SimpleNamespace(async_replace_backup=replace)

    managers = participants(
        **{
            name: manager(name)
            for name in (
                "memory",
                "temporary",
                "knowledge",
                "archive",
                "usage",
                "guest_mode",
                "request_rules",
            )
        }
    )
    managers.usage.request_retention_days = 0
    managers.usage.run_retention_days = 0

    await apply(managers, prepared)

    assert writes == [
        "memory",
        "temporary",
        "knowledge",
        "archive",
        "usage",
        "guest_mode",
        "request_rules",
    ]
    assert state == {
        "memory": ((prepared.memories,), {}),
        "temporary": ((prepared.temporary_memories,), {}),
        "knowledge": ((prepared.knowledge,), {}),
        "archive": (
            (prepared.archive_sessions, prepared.archive_turns),
            {} if active is None else {"active": active},
        ),
        "usage": (
            (
                prepared.usage_totals,
                prepared.usage_daily,
                prepared.usage_requests,
                prepared.usage_runs,
            ),
            {},
        ),
        "guest_mode": ((prepared.guest_mode_schedule,), {}),
        "request_rules": ((prepared.request_rules,), {}),
    }


@pytest.mark.parametrize("active", [None, {}, {"conversation:browser": "session"}])
async def test_apply_prepared_preserves_category_and_local_binding_contract(active):
    await _assert_application_contract(restore_recovery._apply_prepared, active)


@pytest.mark.parametrize(
    "mutation",
    ["skip_knowledge", "reorder_memory", "drop_empty_active", "skip_retention"],
)
async def test_category_contract_detects_deliberate_regressions(mutation):
    source = inspect.getsource(restore_recovery._apply_prepared)
    if mutation == "skip_knowledge":
        old = "    await managers.knowledge.async_replace_backup(prepared.knowledge)\n"
        new = ""
    elif mutation == "reorder_memory":
        old = "    await managers.memory.async_replace_backup(prepared.memories)\n    await managers.temporary.async_replace_backup(prepared.temporary_memories)\n"
        new = "    await managers.temporary.async_replace_backup(prepared.temporary_memories)\n    await managers.memory.async_replace_backup(prepared.memories)\n"
    elif mutation == "drop_empty_active":
        old = "if prepared.archive_active is not None"
        new = "if prepared.archive_active"
    else:
        old = "managers.usage.request_retention_days = int("
        new = "managers.usage.ignored_retention_days = int("
    assert source.count(old) == 1
    namespace = dict(vars(restore_recovery))
    exec(
        compile(source.replace(old, new), "<restore-contract-mutation>", "exec"),
        namespace,
    )
    with pytest.raises(AssertionError):
        await _assert_application_contract(namespace["_apply_prepared"], {})
