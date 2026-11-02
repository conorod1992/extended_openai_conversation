"""Serialized restore/recovery and startup share persisted Function semantics."""

import asyncio
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

import pytest
import yaml

from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
    function_configuration_repair_state,
    has_unavailable_native_tool,
)
from custom_components.extended_openai_conversation_responses.native_function_schema_migration import (
    migrate_legacy_stock_native_function_tools_yaml,
)
from tests.function_configuration_corpus import (
    FUNCTION_CONFIGURATION_CASES,
    corpus_config,
)
from tests.test_native_function_schema_startup_migration import (
    _historical_execute_service_preset,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_selective_restore_concurrency import RestoreHarness


def _restart_and_migrate(restored):
    """Drop object identities through the JSON persistence boundary before startup."""
    saved = json.loads(json.dumps(dict(restored.data)))
    affected = SimpleNamespace(
        subentry_id="restored",
        subentry_type="conversation",
        title="Restored",
        data=saved,
    )
    healthy = SimpleNamespace(
        subentry_id="healthy",
        subentry_type="conversation",
        title="Healthy",
        data={
            "functions": yaml.safe_dump([_historical_execute_service_preset()]),
            "function_groups": [],
            "prompt": "Healthy sibling",
        },
    )
    entry = SimpleNamespace(
        entry_id="restart-entry",
        version=integration.CONFIG_ENTRY_VERSION,
        disabled_by=None,
        subentries={"restored": affected, "healthy": healthy},
    )
    writes = []

    def update(parent, child, *, data):
        assert parent is entry
        writes.append(child.subentry_id)
        child.data = deepcopy(data)

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda _domain: [entry],
            async_update_subentry=update,
        )
    )
    return hass, affected, healthy, writes


def _assert_effective_source(case, source):
    state = function_configuration_repair_state(source)
    if case["effective"] == "quarantined":
        assert state is not None and len(state.invalid) == 1
        assert (
            state.invalid[0]["tool"] == yaml.safe_load(case["config"]["functions"])[0]
        )
        assert state.valid == []
    else:
        assert state is None
        tools = configured_function_tools_from_data(source)
        if case["effective"] == "empty":
            assert tools == []
        elif case["effective"] == "disabled":
            assert len(tools) == 1 and tools[0]["enabled"] is False
            variables = tools[0]["function"]["variables"].as_dict()
            assert variables["literal"] == "1e-06"
            assert variables["day"] == date(2026, 10, 17)
            assert type(variables["factor"]) is float and variables["factor"] == 1e-6
        else:
            assert any(tool["spec"]["name"] == "get_attributes" for tool in tools)


@pytest.mark.parametrize(
    "case", FUNCTION_CONFIGURATION_CASES, ids=lambda case: case["id"]
)
async def test_serialized_restore_restarts_with_healthy_sibling_and_exact_source(
    monkeypatch, hass, case
):
    tx = RestoreHarness(monkeypatch)
    document = private_document()
    incoming = {
        **corpus_config(case),
        "prompt": "Recovered source",
        "future_field": {"opaque": "preserved"},
    }
    document["agent"]["config"] = deepcopy(incoming)
    artifact = backup.finalize_backup_snapshot(document)["json"]
    await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        artifact,
        sections=[transfer.SECTION_CONFIGURATION],
    )
    assert tx.subentry.data == incoming
    assert tx.journal.data is None

    restarted, affected, healthy, writes = _restart_and_migrate(tx.subentry)
    await integration.async_migrate_integration(restarted)
    assert affected.data == incoming
    _assert_effective_source(case, affected.data)
    migrated = configured_function_tools_from_data(healthy.data)[0]
    assert migrated["spec"]["strict"] is False
    assert migrated["enabled"] is False
    assert writes == ["healthy"]
    # Repeated startup cannot rewrite or silently enable retained data.
    after = deepcopy(healthy.data)
    await integration.async_migrate_integration(restarted)
    assert healthy.data == after and affected.data == incoming
    assert writes == ["healthy"]


@pytest.mark.parametrize(
    "case", FUNCTION_CONFIGURATION_CASES, ids=lambda case: case["id"]
)
@pytest.mark.parametrize("phase", ["committed", "applying"])
async def test_serialized_journal_recovery_keeps_representation_and_quarantine(
    monkeypatch, hass, case, phase
):
    tx = RestoreHarness(monkeypatch)
    source = {**corpus_config(case), "prompt": "Before restore"}
    tx.subentry.data = deepcopy(source)
    document = private_document()
    document["agent"]["config"] = {**deepcopy(source), "prompt": "After restore"}
    await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        backup.finalize_backup_snapshot(document)["json"],
        sections=[transfer.SECTION_CONFIGURATION],
    )
    pending = json.loads(json.dumps(tx.journal.saved[-1]))
    pending["phase"] = phase
    tx.journal.data = pending
    await restore_recovery.async_recover_pending_restore(tx.hass, tx.entry, tx.subentry)
    expected = source if phase == "applying" else document["agent"]["config"]
    assert tx.subentry.data == expected
    assert tx.journal.data is None
    _assert_effective_source(case, tx.subentry.data)
    assert (
        await restore_recovery.async_recover_pending_restore(
            tx.hass, tx.entry, tx.subentry
        )
        is False
    )


@pytest.mark.parametrize(
    "name", [[], {}, ["execute_service"], {"legacy": "execute_service"}]
)
def test_native_repair_preflight_handles_malformed_names_without_rewriting(name):
    tools = [
        {
            "spec": {"name": "retained_invalid"},
            "function": {"type": "native", "name": name},
        }
    ]
    source = {"functions": yaml.safe_dump(tools)}
    before = deepcopy(source)
    assert has_unavailable_native_tool(source) is True
    assert source == before


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in FUNCTION_CONFIGURATION_CASES
        if case["effective"] == "quarantined"
    ],
    ids=lambda case: case["id"],
)
def test_stock_migration_preserves_quarantined_and_disabled_typed_siblings(case):
    invalid = yaml.safe_load(case["config"]["functions"])[0]
    disabled = next(
        case for case in FUNCTION_CONFIGURATION_CASES if case["effective"] == "disabled"
    )
    typed = yaml.safe_load(disabled["config"]["functions"])[0]
    source = yaml.safe_dump([invalid, _historical_execute_service_preset(), typed])
    migrated, changed = migrate_legacy_stock_native_function_tools_yaml(source)
    assert changed is True
    tools = yaml.safe_load(migrated)
    assert tools[0] == invalid and tools[2] == typed
    assert all(tool["enabled"] is False for tool in tools)
    assert tools[1]["spec"]["strict"] is False
    assert migrate_legacy_stock_native_function_tools_yaml(migrated) == (
        migrated,
        False,
    )


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("programming failure"),
        OSError("storage failure"),
        asyncio.CancelledError(),
    ],
)
async def test_migration_does_not_mask_unexpected_failures(monkeypatch, error):
    restarted, _affected, _healthy, writes = _restart_and_migrate(
        SimpleNamespace(data={})
    )

    def fail(*_args):
        raise error

    monkeypatch.setattr(integration, "_migrate_saved_agent_defaults", fail)
    with pytest.raises(type(error)) as raised:
        await integration.async_migrate_integration(restarted)
    assert raised.value is error
    assert writes == []
