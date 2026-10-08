"""Persistence regressions independently observe data and provider accounting."""

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util
from custom_components.extended_openai_conversation_responses import conversation
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.management_history_queries import (
    usage_runs_page,
    usage_requests_page,
)
from tests.test_memory import _memory, FakeStorage
from tests.test_usage_detail_lifecycle import _manager, _run, _request, DetailStorage


async def test_disabled_archive_still_prunes_existing_storage(monkeypatch):
    archive = SimpleNamespace(async_prune=AsyncMock())
    get_archive = AsyncMock(return_value=archive)
    monkeypatch.setattr(conversation, "async_get_archive", get_archive)

    @asynccontextmanager
    async def lease(_entity):
        yield

    monkeypatch.setattr(conversation, "conversation_request_lease", lease)
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = SimpleNamespace()
    entity.entry = SimpleNamespace(entry_id="entry")
    entity.subentry = SimpleNamespace(
        subentry_id="agent", data={"archive_retention_days": 2}
    )
    entity._archive = None
    await entity._async_prune_archive_retention()
    archive.async_prune.assert_awaited_once_with(2)
    assert entity._archive is None  # cleanup does not enable collection


@pytest.mark.parametrize("failed", [False, True])
async def test_embedding_requests_account_for_usage_and_failures(failed):
    usage = _manager(DetailStorage())
    await usage.async_initialize()
    create = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[1.0, 0.0])],
            usage=SimpleNamespace(prompt_tokens=123, total_tokens=123),
        )
    )
    if failed:
        create.side_effect = RuntimeError("provider unavailable")
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.subentry = SimpleNamespace(data={})
    entity.entry = SimpleNamespace(data={"api_provider": "openai"})
    entity.entry.runtime_data = SimpleNamespace(
        embeddings=SimpleNamespace(create=create)
    )
    entity._usage = usage
    async with usage.async_run():
        if failed:
            with pytest.raises(RuntimeError):
                await entity._async_create_embeddings(["tea"], model="embedding-test")
        else:
            assert await entity._async_create_embeddings(
                ["tea"], model="embedding-test"
            ) == [[1.0, 0.0]]
    assert usage.as_dict()["api_request_count"] == 1
    assert usage.latest_run.request_count == 1
    assert usage.as_dict()["total_tokens"] == (0 if failed else 123)
    assert usage.latest_run.total_tokens == (0 if failed else 123)
    assert usage.requests[0].api_mode == "embeddings"
    assert usage.requests[0].successful is not failed


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "field,value",
    [
        ("base_url", "https://provider-b.example/v1"),
        ("api_provider", "azure"),
        ("api_version", "next-version"),
    ],
)
async def test_same_model_different_embedding_spaces_rebuild_cache_after_restart(
    restart, field, value
):
    storage, cache = FakeStorage(), FakeStorage()
    from custom_components.extended_openai_conversation_responses.memory import (
        PersistentMemory,
    )

    memory = PersistentMemory(storage, cache)
    await memory.async_initialize()
    await memory.async_add("user", "Tea preference", "preference", "explicit")
    provider_a = AsyncMock(return_value=[[1.0, 0.0]])
    from custom_components.extended_openai_conversation_responses.agent_configuration import (
        sync_memory_embedding_provider,
    )

    entity = SimpleNamespace(
        _memory=memory,
        entry=SimpleNamespace(
            data={"api_provider": "openai", "base_url": "https://provider-a.example/v1"}
        ),
        subentry=SimpleNamespace(
            data={
                "memory_retrieval_mode": "hybrid",
                "memory_embedding_model": "same-model",
            }
        ),
        _async_create_embeddings=provider_a,
    )
    sync_memory_embedding_provider(entity)
    await memory.async_prepare_hybrid(["user"], "tea")
    restarted = PersistentMemory(storage, cache) if restart else memory
    await restarted.async_initialize()
    provider_b = AsyncMock(return_value=[[0.0, 1.0]])
    entity._memory = restarted
    entity.entry.data[field] = value
    entity._async_create_embeddings = provider_b
    sync_memory_embedding_provider(entity)
    await restarted.async_prepare_hybrid(["user"], "tea")
    assert provider_b.await_count == 2  # stored content and query both use B
    assert all(item.get("space_id") for item in cache.data["embeddings"].values())


async def test_idle_usage_read_paths_hide_expired_details(freezer):
    manager = _manager(DetailStorage(), request_retention_days=1, run_retention_days=1)
    await manager.async_initialize()
    now = dt_util.utcnow()
    manager.runs = [_run("run-one", now.isoformat())]
    manager.requests = [_request("one", now.isoformat())]
    assert usage_runs_page(manager)["runs"]
    freezer.move_to(now + timedelta(days=2))
    assert usage_runs_page(manager)["runs"] == []
    assert usage_requests_page(manager, "run-one")["requests"] == []
    assert manager.latest_run is None


async def test_legacy_embedding_cache_regenerates_and_unchanged_space_reuses_it():
    from custom_components.extended_openai_conversation_responses.memory import (
        PersistentMemory,
    )

    storage, cache = FakeStorage(), FakeStorage()
    memory = PersistentMemory(storage, cache)
    await memory.async_initialize()
    await memory.async_add("user", "Tea preference", "preference", "explicit")
    provider = AsyncMock(return_value=[[1.0, 0.0]])
    memory.set_embedding_provider(provider, "same-model", space_id="known-space")
    await memory.async_prepare_hybrid(["user"], "tea")
    provider.reset_mock()
    await memory.async_prepare_hybrid(["user"], "tea")
    assert provider.await_count == 1
    for item in cache.data["embeddings"].values():
        item.pop("space_id")
    restarted = PersistentMemory(storage, cache)
    await restarted.async_initialize()
    restarted.set_embedding_provider(provider, "same-model", space_id="known-space")
    provider.reset_mock()
    await restarted.async_prepare_hybrid(["user"], "tea")
    assert provider.await_count == 2
