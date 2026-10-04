"""Memory provenance, search pagination and Usage sensor notification regressions."""

from datetime import timedelta

import pytest

from homeassistant.util import dt as dt_util
from tests.test_memory import _memory
from tests.test_usage_detail_lifecycle import DetailStorage, _manager, _run


@pytest.mark.parametrize("keyed", [True, False])
async def test_explicit_confirmation_promotes_implicit_memory_and_survives_implicit_retry(
    keyed,
):
    memory = await _memory()
    extra = {"key": "preference.drink"} if keyed else {}
    created = await memory.async_upsert(
        "user", "I prefer tea", "preference", "implicit", **extra
    )
    confirmed = await memory.async_upsert(
        "user", "I prefer tea", "preference", "explicit", **extra
    )
    assert confirmed["status"] == "updated"
    assert confirmed["memory"]["memory_id"] == created["memory"]["memory_id"]
    assert confirmed["memory"]["source"] == "explicit"
    retry = await memory.async_upsert(
        "user", "I prefer tea", "preference", "implicit", **extra
    )
    assert retry["status"] == "unchanged"
    assert retry["memory"]["source"] == "explicit"


async def test_implicit_update_cannot_use_existing_explicit_source_to_bypass_privacy():
    memory = await _memory()
    await memory.async_upsert(
        "user", "I prefer tea", "preference", "explicit", key="preference.drink"
    )
    with pytest.raises(ValueError):
        await memory.async_upsert(
            "user", "I have a medical diagnosis of diabetes", "health", "implicit", key="preference.drink"
        )


async def test_search_offset_returns_disjoint_ranked_pages():
    memory = await _memory()
    for index in range(6):
        await memory.async_add(
            "user", f"Tea preference number {index}", "preference", "explicit"
        )
    ranked = await memory.async_search("user", "tea", limit=6)
    first = await memory.async_search("user", "tea", limit=2, offset=0)
    second = await memory.async_search("user", "tea", limit=2, offset=2)
    third = await memory.async_search("user", "tea", limit=2, offset=4)
    assert first + second + third == ranked
    assert len({record.memory_id for record in first + second + third}) == 6
    assert await memory.async_search("user", "tea", offset=99) == []


@pytest.mark.parametrize("operation", ["clear", "prune"])
async def test_usage_detail_mutation_notifies_after_latest_run_changes(operation):
    manager = _manager(DetailStorage(), run_retention_days=1)
    manager.runs = [_run("expired", (dt_util.utcnow() - timedelta(days=3)).isoformat())]
    latest = []
    manager.async_add_listener(lambda: latest.append(manager.latest_run))
    if operation == "clear":
        await manager.async_clear_details(confirm=True)
    else:
        await manager.async_prune_details()
    assert latest == [None]
    assert manager.runs == []


async def test_failed_clear_does_not_notify_or_remove_latest_run():
    details = DetailStorage()
    manager = _manager(details)
    run = _run("retained", dt_util.utcnow().isoformat())
    manager.runs = [run]
    latest = []
    manager.async_add_listener(lambda: latest.append(manager.latest_run))
    details.fail_save = True
    with pytest.raises(OSError):
        await manager.async_clear_details(confirm=True)
    assert not latest
    assert manager.latest_run is run


async def test_implicit_keyed_write_cannot_replace_an_explicit_fact():
    memory = await _memory()
    confirmed = await memory.async_upsert("user", "I prefer tea", "preference", "explicit", key="preference.drink")
    inferred = await memory.async_upsert("user", "I prefer coffee", "preference", "implicit", key="preference.drink")
    assert inferred["status"] == "needs_resolution"
    assert inferred["candidate"] == confirmed["memory"]
    retained = await memory.async_search("user", "tea")
    assert [record.content for record in retained] == ["I prefer tea"]
    assert retained[0].source == "explicit"


@pytest.mark.parametrize("keyed", [True, False])
async def test_implicit_duplicate_cannot_reclassify_confirmed_fact(keyed):
    memory = await _memory()
    extra = {"key": "preference.drink"} if keyed else {}
    confirmed = await memory.async_upsert("user", "I prefer tea", "preference", "explicit", **extra)
    inferred = await memory.async_upsert("user", "I prefer tea", "other", "implicit", subject="inferred", **extra)
    assert inferred["status"] == "unchanged"
    assert inferred["memory"] == confirmed["memory"]
