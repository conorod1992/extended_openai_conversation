"""Targeted regression coverage for lifecycle, recovery, and adapter edge paths."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    model_lifecycle,
    prompt_cache,
    skill_transactions,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.functions import composite
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)


class _FakeStore:
    def __init__(self, *_args, **_kwargs):
        self.delay_cleaned = False
        self.final_cleaned = False
        self.removed = False

    def _async_cleanup_delay_listener(self) -> None:
        self.delay_cleaned = True

    def _async_cleanup_final_write_listener(self) -> None:
        self.final_cleaned = True

    async def async_remove(self) -> None:
        self.removed = True


@pytest.mark.asyncio
async def test_agent_deletion_cancels_owned_background_work_and_fences_agent(
    hass, monkeypatch
) -> None:
    """Deleting an agent drains manager work and makes repeated deletion idempotent."""
    store = _FakeStore()
    prune_started = asyncio.Event()

    async def pending_prune() -> None:
        prune_started.set()
        await asyncio.Event().wait()

    prune_task = asyncio.create_task(pending_prune())
    await prune_started.wait()
    manager = SimpleNamespace(
        _store=store,
        _prune_task=prune_task,
        _prune_save_task=None,
        _stopping=False,
    )
    key = ("entry-1", "agent-1")
    hass.data[f"{DOMAIN}.memory_managers"] = {key: manager}

    monkeypatch.setattr(agent_deletion, "Store", _FakeStore)
    monkeypatch.setattr(agent_deletion, "PropagatingWriteStore", _FakeStore)

    from custom_components.extended_openai_conversation_responses import model_lifecycle as lifecycle

    cleared = Mock()
    monkeypatch.setattr(lifecycle, "clear_retirement_failure", cleared)

    await agent_deletion.async_delete_agent_data(hass, *key)

    gate = agent_deletion.get_agent_maintenance_gate(hass, *key)
    assert prune_task.cancelled()
    assert manager._stopping is True
    assert store.delay_cleaned is True
    assert store.final_cleaned is True
    assert store.removed is True
    assert key not in hass.data[f"{DOMAIN}.memory_managers"]
    assert gate.deleted is True
    cleared.assert_called_once_with(hass, entry_id="entry-1", subentry_id="agent-1")

    # The deletion fence makes retries harmless and prevents cleanup replay.
    await agent_deletion.async_delete_agent_data(hass, *key)
    cleared.assert_called_once()


def test_agent_storage_discovery_is_exact_and_partition_aware(hass, tmp_path) -> None:
    storage = tmp_path / ".storage"
    storage.mkdir()
    matching = {
        f"{DOMAIN}.memory.entry-1.agent-1",
        f"{DOMAIN}.archive.entry-1.agent-1.2026-10",
        f"{DOMAIN}.knowledge.entry-1.agent-1.embedding-cache",
    }
    other = {
        f"{DOMAIN}.memory.entry-1.agent-10",
        f"{DOMAIN}.memory.entry-2.agent-1",
        "unrelated.file",
    }
    for name in matching | other:
        (storage / name).write_text("x")

    assert set(agent_deletion._storage_names(hass, "entry-1", "agent-1")) == matching
    assert set(agent_deletion._storage_names(hass, "entry-1")) == matching | {
        f"{DOMAIN}.memory.entry-1.agent-10"
    }


@pytest.mark.asyncio
async def test_entry_deletion_collects_live_known_and_orphaned_agent_ids(
    hass, monkeypatch
) -> None:
    entry = SimpleNamespace(entry_id="entry-1", subentries={"live-agent": object()})
    hass.data[agent_deletion.KNOWN_AGENTS] = {
        entry.entry_id: {"live-agent", "known-orphan"}
    }
    monkeypatch.setattr(
        agent_deletion,
        "_storage_names",
        lambda *_args: [
            f"{DOMAIN}.archive.entry-1.disk-orphan.2026-10",
            f"{DOMAIN}.memory.entry-1.live-agent",
        ],
    )
    deleted = AsyncMock()
    monkeypatch.setattr(agent_deletion, "async_delete_agent_data", deleted)

    await agent_deletion.async_delete_entry_data(hass, entry)

    assert {
        call.args[2] for call in deleted.await_args_list
    } == {"live-agent", "known-orphan", "disk-orphan"}
    assert entry.entry_id not in hass.data[agent_deletion.KNOWN_AGENTS]


@pytest.mark.asyncio
async def test_removed_subentry_cleanup_tracks_current_generation(hass, monkeypatch) -> None:
    entry = SimpleNamespace(entry_id="entry-1", subentries={"current": object()})
    hass.data[agent_deletion.KNOWN_AGENTS] = {
        entry.entry_id: {"current", "removed"}
    }
    deleted = AsyncMock()
    monkeypatch.setattr(agent_deletion, "async_delete_agent_data", deleted)

    await agent_deletion.async_delete_removed_subentries(hass, entry)

    deleted.assert_awaited_once_with(hass, entry.entry_id, "removed")
    assert hass.data[agent_deletion.KNOWN_AGENTS][entry.entry_id] == {"current"}


@pytest.mark.asyncio
async def test_prompt_cache_proxy_moves_sdk_unsupported_options_into_extra_body() -> None:
    """The client proxy preserves caller extra_body while sending cache controls."""
    create = AsyncMock(return_value="ok")
    delegate = SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=object(),
        embeddings=object(),
    )
    proxy = prompt_cache.PerformanceOpenAIClientProxy(delegate, direct_openai=True)
    context = prompt_cache.PromptCacheContext(prefix="stable\n", key="eoc-test")
    token = prompt_cache._PROMPT_CACHE_CONTEXT.set(context)
    try:
        result = await proxy.responses.create(
            model="gpt-5.6",
            input=[
                {
                    "type": "message",
                    "role": "system",
                    "content": "stable\nvolatile",
                }
            ],
            extra_body={"caller": True},
        )
    finally:
        prompt_cache._PROMPT_CACHE_CONTEXT.reset(token)

    assert result == "ok"
    kwargs = create.await_args.kwargs
    assert "prompt_cache_options" not in kwargs
    assert kwargs["prompt_cache_key"] == "eoc-test"
    assert kwargs["extra_body"] == {
        "caller": True,
        "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
    }
    assert kwargs["input"][0]["content"][0]["prompt_cache_breakpoint"] == {
        "mode": "explicit"
    }


@pytest.mark.asyncio
async def test_prompt_cache_proxy_does_not_override_caller_extra_body_policy() -> None:
    create = AsyncMock(return_value="ok")
    delegate = SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=object(),
        embeddings=object(),
    )
    proxy = prompt_cache.PerformanceOpenAIClientProxy(delegate, direct_openai=True)
    token = prompt_cache._PROMPT_CACHE_CONTEXT.set(
        prompt_cache.PromptCacheContext(prefix="stable", key="eoc-test")
    )
    try:
        await proxy.responses.create(
            model="gpt-5.6",
            input=[
                {
                    "type": "message",
                    "role": "system",
                    "content": "stable",
                }
            ],
            extra_body={"prompt_cache_options": {"mode": "caller"}},
        )
    finally:
        prompt_cache._PROMPT_CACHE_CONTEXT.reset(token)

    assert create.await_args.kwargs["extra_body"]["prompt_cache_options"] == {
        "mode": "caller"
    }


def test_composite_resource_guard_rejects_recursive_configuration() -> None:
    root = {"type": "composite"}
    root["sequence"] = [root]

    with pytest.raises(HomeAssistantError, match="recursive configuration"):
        composite._validate_resources(root)


def test_composite_resource_guard_rejects_oversized_immediate_sequence() -> None:
    root = {
        "type": "composite",
        "sequence": [{"type": "template"} for _ in range(composite.MAX_COMPOSITE_FUNCTIONS + 1)],
    }

    with pytest.raises(HomeAssistantError, match="depth/node safety limits"):
        composite._validate_resources(root)


def test_composite_resource_guard_rejects_excessive_depth() -> None:
    root = current = {"type": "composite", "sequence": []}
    for _ in range(composite.MAX_COMPOSITE_DEPTH + 1):
        child = {"type": "composite", "sequence": []}
        current["sequence"] = [child]
        current = child

    with pytest.raises(HomeAssistantError, match="depth/node safety limits"):
        composite._validate_resources(root)


def _skill_transaction(tmp_path):
    installed = tmp_path / "installed"
    installed.mkdir()
    root = tmp_path / ".staging"
    target = installed / "owned"
    target.mkdir()
    (target / "SKILL.md").write_text("OLD")
    staged = root / "candidate"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text("NEW")
    backup = root / f"owned.backup-{'a' * 32}"
    journal = skill_transactions.prepare_transaction(root, target, backup, staged)
    return installed, root, target, staged, backup, journal


def test_skill_recovery_rejects_symlinked_owned_paths(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(HomeAssistantError, match="symbolic links"):
        skill_transactions._identity(link)
    with pytest.raises(HomeAssistantError, match="symbolic link"):
        skill_transactions._remove(link)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("backup", "wrong.backup", "backup ownership"),
        ("staged", None, "candidate path is missing"),
        ("previous_identity", ["bad", 1], "identity is invalid"),
        ("candidate_identity", None, "candidate ownership is missing"),
    ],
)
def test_skill_recovery_rejects_invalid_journal_ownership(
    tmp_path, field, value, message
) -> None:
    installed, _root, _target, _staged, _backup, journal = _skill_transaction(tmp_path)
    document = json.loads(journal.read_text())
    document[field] = value
    journal.write_text(json.dumps(document))

    with pytest.raises(HomeAssistantError, match=message):
        skill_transactions.recover_transaction(journal, installed)


def test_skill_recovery_rejects_symlink_journal_in_directory_scan(tmp_path) -> None:
    installed = tmp_path / "installed"
    installed.mkdir()
    root = tmp_path / ".staging"
    root.mkdir()
    real = tmp_path / "real.json"
    real.write_text("{}")
    journal = root / f"transaction-{'a' * 32}.json"
    journal.symlink_to(real)

    with pytest.raises(HomeAssistantError, match="journal must not be a symbolic link"):
        skill_transactions.recover_transactions(root, installed)


def test_model_lifecycle_handles_invalid_dates_and_configured_model(monkeypatch) -> None:
    monkeypatch.setattr(
        model_lifecycle,
        "model_metadata",
        lambda _model: {
            "status": "deprecated",
            "deprecated_at": "not-a-date",
            "shutdown_at": "",
            "lifecycle_note": "legacy",
        },
    )
    subentry = SimpleNamespace(data={"chat_model": "  test-model  "})

    snapshot = model_lifecycle.configured_lifecycle(subentry)

    assert snapshot["model"] == "test-model"
    assert snapshot["deprecated_at"] is None
    assert snapshot["shutdown_at"] is None
    assert snapshot["shutdown_reached"] is False


def test_model_lifecycle_warning_is_once_per_runtime(monkeypatch) -> None:
    hass = SimpleNamespace(data={})
    warning = Mock()
    monkeypatch.setattr(model_lifecycle._LOGGER, "warning", warning)
    lifecycle = {
        "status": "deprecated",
        "model": "gpt-old",
        "shutdown_at": "2027-01-01",
    }

    model_lifecycle.log_deprecation_once(
        hass,
        entry_id="entry",
        subentry_id="agent",
        title="Kitchen",
        lifecycle=lifecycle,
    )
    model_lifecycle.log_deprecation_once(
        hass,
        entry_id="entry",
        subentry_id="agent",
        title="Kitchen",
        lifecycle=lifecycle,
    )

    warning.assert_called_once()


def test_clear_retirement_failure_ignores_absent_or_different_model(monkeypatch) -> None:
    hass = SimpleNamespace(data={})
    delete = Mock()
    monkeypatch.setattr(model_lifecycle.ir, "async_delete_issue", delete)

    model_lifecycle.clear_retirement_failure(
        hass, entry_id="entry", subentry_id="agent", model="gpt-old"
    )
    hass.data[model_lifecycle._DATA_FAILURES] = {
        ("entry", "agent"): {"model": "gpt-new"}
    }
    model_lifecycle.clear_retirement_failure(
        hass, entry_id="entry", subentry_id="agent", model="gpt-old"
    )

    assert hass.data[model_lifecycle._DATA_FAILURES]
    delete.assert_not_called()


def test_retirement_provider_error_accepts_supported_404_wording() -> None:
    assert model_lifecycle.retirement_provider_error(
        ProviderStreamError(
            "Requested model is not available",
            code="resource_not_found",
            status_code=404,
        )
    )


def test_frontend_version_module_is_covered() -> None:
    from custom_components.extended_openai_conversation_responses.frontend_version import (
        FRONTEND_VERSION,
    )

    assert FRONTEND_VERSION
