"""An ordinary full archive restore leaves the global HA loop responsive."""

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    async_get_durable_usage,
)
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401
from tests_real_ha.test_backup_transfer_protocol import (
    _entry,
    _setup_entry,
    _conversation_subentry,
    _user_token,
    _download_archive,
    _upload_archive,
    _transfer_call,
)


@pytest.mark.timeout(120)
@pytest.mark.parametrize("count", [0, 2000])
async def test_full_archive_restore_remains_responsive(
    hass, hass_ws_client, real_store_io, count
):
    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    usage = await async_get_durable_usage(hass, entry.entry_id, subentry.subentry_id)
    for _ in range(count):
        async with usage.async_run():
            await usage.async_record_request(
                successful=True,
                usage=RequestUsage(input_tokens=30, output_tokens=10, total_tokens=40),
            )
    admin = MockUser(id="restore-latency-owner", name="Restore latency", is_owner=True)
    client = await hass_ws_client(hass, await _user_token(hass, admin))
    archive, metadata = await _download_archive(client, entry=entry, mode="full")
    session = await _upload_archive(
        client, entry=entry, archive=archive, filename=metadata["filename"]
    )
    inspected = await _transfer_call(
        client, entry=entry, action="import_inspect", data={"session_id": session}
    )
    assert inspected["success"], inspected
    # Upload, inspection and large frame encoding finish before the measured request.
    gaps = []
    running = True
    loop = asyncio.get_running_loop()
    previous_debug = loop.get_debug()
    loop.set_debug(False)

    async def heartbeat():
        previous = loop.time()
        while running:
            await asyncio.sleep(0.005)
            now = loop.time()
            gaps.append(now - previous)
            previous = now

    pulse = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)
    try:
        restored = await _transfer_call(
            client,
            entry=entry,
            action="import_restore",
            data={
                "session_id": session,
                "preview_token": inspected["result"]["preview_token"],
            },
        )
        await asyncio.sleep(0.01)
    finally:
        running = False
        await pulse
        loop.set_debug(previous_debug)
    assert restored["success"], restored
    current = await async_get_durable_usage(hass, entry.entry_id, subentry.subentry_id)
    assert len(current.requests) == len(current.runs) == count
    assert current.totals.total_tokens == count * 40
    assert max(gaps) < 0.25, (
        f"Unrelated HA work stalled for {max(gaps):.3f}s with {count} requests"
    )
