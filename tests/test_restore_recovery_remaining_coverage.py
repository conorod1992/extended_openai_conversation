"""Focused coverage for restore-recovery durability and runtime reconciliation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import restore_recovery


class _JournalStore:
    """Minimal Store stand-in for journal verification tests."""

    def __init__(self, *, persisted=None, save_error=None, load_error=None) -> None:
        self.persisted = persisted
        self.save_error = save_error
        self.load_error = load_error
        self.saved = None

    async def async_save(self, value) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.saved = value

    async def async_load(self):
        if self.load_error is not None:
            raise self.load_error
        return self.persisted


@pytest.mark.parametrize("failure_at", ["save", "load"])
async def test_journal_verification_unavailable_preserves_underlying_failure(failure_at) -> None:
    """An unreadable durability decision is distinct from a verified mismatch."""
    error = OSError(f"{failure_at} unavailable")
    store = _JournalStore(
        persisted={"phase": "applying"},
        save_error=error if failure_at == "save" else None,
        load_error=error if failure_at == "load" else None,
    )

    with pytest.raises(restore_recovery._JournalVerificationUnavailable) as exc_info:
        await restore_recovery._async_write_journal_verified(
            store, {"phase": "applying"}
        )

    assert exc_info.value.__cause__ is error


async def test_journal_verification_requires_exact_read_back() -> None:
    """A swallowed or stale Store write must not count as durable journal state."""
    journal = {"phase": "committed", "transaction_id": "tx-1"}
    store = _JournalStore(persisted={**journal, "phase": "applying"})

    assert await restore_recovery._async_write_journal_verified(store, journal) is False
    assert store.saved == journal


async def test_durable_managers_bypasses_volatile_usage_fallback(monkeypatch) -> None:
    """Restore must resolve the original durable Usage manager, never fallback RAM."""
    from custom_components.extended_openai_conversation_responses import (
        conversation_archive,
        guest_mode,
        knowledge,
        memory,
        request_rules,
        runtime_failure_hardening,
        temporary_memory,
    )

    expected = [object() for _ in range(7)]
    getters = [
        (memory, "async_get_memory"),
        (temporary_memory, "async_get_temporary_memory"),
        (knowledge, "async_get_knowledge"),
        (conversation_archive, "async_get_archive"),
        (runtime_failure_hardening, "_ORIGINAL_ASYNC_GET_USAGE"),
        (guest_mode, "async_get_guest_mode"),
        (request_rules, "async_get_request_rules"),
    ]

    mocks = []
    for (module, name), value in zip(getters, expected, strict=True):
        mock = AsyncMock(return_value=value)
        monkeypatch.setattr(module, name, mock)
        mocks.append(mock)

    hass = object()
    result = await restore_recovery._durable_managers(hass, "entry-1", "agent-1")

    assert result == tuple(expected)
    for mock in mocks:
        mock.assert_awaited_once_with(hass, "entry-1", "agent-1")



def _continuity_runtime(marker: str):
    return SimpleNamespace(
        _sessions={marker: object()},
        _memory_bundles={marker: object()},
        _pending_ends={marker},
        _ignored_conversation_ids={marker: None},
    )


def _rule_runtime(marker: str):
    return SimpleNamespace(_conversation_overrides={marker: object()})


def _group_runtime(marker: str):
    return SimpleNamespace(
        _sessions={marker: object()},
        _last_request={marker: object()},
    )


def test_runtime_reset_reconciles_distinct_registry_and_agent_objects(monkeypatch) -> None:
    """Restore clears both stale copies then makes the live agent copy authoritative."""
    from custom_components.extended_openai_conversation_responses import (
        continuity,
        function_groups,
        request_rules,
        runtime_failure_hardening,
    )

    key = ("entry-1", "agent-1")
    registry_continuity = _continuity_runtime("registry")
    agent_continuity = _continuity_runtime("agent")
    registry_rules = _rule_runtime("registry")
    agent_rules = _rule_runtime("agent")
    registry_groups = _group_runtime("registry")
    agent_groups = _group_runtime("agent")
    fallback_usage = object()
    unrelated_usage = object()
    durable_usage = object()
    agent = SimpleNamespace(
        _continuity=agent_continuity,
        _request_rule_runtime=agent_rules,
        _function_groups_runtime=agent_groups,
        _usage=unrelated_usage,
    )
    hass = SimpleNamespace(
        data={
            continuity._MANAGERS: {key: registry_continuity},
            request_rules._RUNTIMES: {key: registry_rules},
            function_groups._RUNTIMES: {key: registry_groups},
            runtime_failure_hardening._VOLATILE_USAGE_MANAGERS: {
                key: fallback_usage
            },
            restore_recovery.SUBSYSTEM_STATUS_KEY: {key: {"status": "degraded"}},
        }
    )
    managers = (
        object(),
        object(),
        object(),
        object(),
        durable_usage,
        object(),
        object(),
    )
    monkeypatch.setattr(restore_recovery, "_active_agent", lambda *_args: agent)

    restore_recovery.reset_restored_runtime(hass, *key, managers)

    for runtime in (registry_continuity, agent_continuity):
        assert runtime._sessions == {}
        assert runtime._memory_bundles == {}
        assert runtime._pending_ends == set()
        assert runtime._ignored_conversation_ids == {}
    for runtime in (registry_rules, agent_rules):
        assert runtime._conversation_overrides == {}
    for runtime in (registry_groups, agent_groups):
        assert runtime._sessions == {}
        assert runtime._last_request == {}

    assert hass.data[continuity._MANAGERS][key] is agent_continuity
    assert hass.data[request_rules._RUNTIMES][key] is agent_rules
    assert hass.data[function_groups._RUNTIMES][key] is agent_groups
    assert key not in hass.data[runtime_failure_hardening._VOLATILE_USAGE_MANAGERS]
    # Only the exact volatile fallback pointer may be replaced.
    assert agent._usage is unrelated_usage
    assert key not in hass.data[restore_recovery.SUBSYSTEM_STATUS_KEY]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, False),
        ({"entries": {}}, False),
        ({"entries": [None]}, False),
        (
            {
                "entries": [
                    {"entry_id": "entry-1", "subentries": {}},
                ]
            },
            False,
        ),
        (
            {
                "entries": [
                    {
                        "entry_id": "entry-1",
                        "subentries": [
                            {
                                "subentry_id": "other",
                                "title": "Restored",
                                "data": {"x": 1},
                            }
                        ],
                    }
                ]
            },
            False,
        ),
        (
            {
                "entries": [
                    {
                        "entry_id": "entry-1",
                        "subentries": [
                            {
                                "subentry_id": "agent-1",
                                "title": "Wrong",
                                "data": {"x": 1},
                            }
                        ],
                    }
                ]
            },
            False,
        ),
        (
            {
                "entries": [
                    {
                        "entry_id": "entry-1",
                        "subentries": [
                            {
                                "subentry_id": "agent-1",
                                "title": "Restored",
                                "data": {"x": 1},
                            }
                        ],
                    }
                ]
            },
            True,
        ),
    ],
)
def test_persisted_subentry_match_is_strict_about_core_store_shape(payload, expected) -> None:
    """Recovery cleanup requires the exact target subentry in a valid Core snapshot."""
    prepared = SimpleNamespace(title="Restored", config={"x": 1})

    assert (
        restore_recovery._persisted_subentry_matches(
            payload, "entry-1", "agent-1", prepared
        )
        is expected
    )
