"""Adapter regressions for lossless results, bounded decoding and file lifecycle."""

import asyncio
import gc
import gzip
from pathlib import Path
import sqlite3
import zlib

import aiohttp
import brotli
import pytest
import yaml

from custom_components.extended_openai_conversation_responses.functions import (
    file,
    native,
    sqlite,
    web,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    propagate_function_execution_errors,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("single", [True, False])
def test_duplicate_sqlite_columns_preserve_aliases_and_all_values(tmp_path, single):
    path = tmp_path / "test.db"
    sqlite3.connect(path).close()
    result = sqlite._execute_sqlite_query(
        str(path), "SELECT 1 AS id, 2 AS id, 3 AS id_2, 4 AS id", single, 10
    )
    assert (result if single else result[0]) == {
        "id": 1,
        "id_3": 2,
        "id_2": 3,
        "id_4": 4,
    }


async def test_file_lock_lives_through_waiters_then_is_reclaimed(hass, tmp_path):
    path = tmp_path / "missing.txt"
    lock = file._get_edit_lock(hass, path)
    await lock.acquire()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def waiting():
        async with file._get_edit_lock(hass, path):
            entered.set()
            await release.wait()

    task = asyncio.create_task(waiting())
    await asyncio.sleep(0)
    assert file._get_edit_lock(hass, path) is lock
    lock.release()
    del lock
    gc.collect()
    await entered.wait()
    assert len(hass.data[file._FILE_EDIT_LOCKS]) == 1
    release.set()
    await task
    gc.collect()
    assert len(hass.data[file._FILE_EDIT_LOCKS]) == 0
    for index in range(100):
        file._get_edit_lock(hass, tmp_path / str(index))
    assert len(hass.data[file._FILE_EDIT_LOCKS]) == 0


def test_relative_allow_dir_uses_ha_configuration_not_cwd(hass, tmp_path, monkeypatch):
    directory = Path(hass.config.config_dir) / "allowed"
    directory.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    function = file.ReadFileFunction()
    assert (
        function._resolve_path(hass, str(directory / "x.txt"), ["allowed"])
        == (directory / "x.txt").resolve()
    )
    with pytest.raises(PermissionError):
        function._resolve_path(hass, str(elsewhere / "allowed" / "x.txt"), ["allowed"])


@pytest.mark.parametrize(
    "header,encode",
    [
        ("gzip, deflate", lambda b: zlib.compress(gzip.compress(b))),
        ("deflate, gzip", lambda b: gzip.compress(zlib.compress(b))),
        (" GZIP , identity ", gzip.compress),
    ],
)
def test_stacked_content_encodings_are_decoded_in_reverse(header, encode):
    original = b"hello world" * 10
    assert web._decode_content_encodings(encode(original), header, 4096) == original


def test_stacked_decoding_bounds_intermediate_body():
    # The outer body is small, but the intermediate body is too large even though
    # it is not a valid inner stream. Size rejection must precede inner decoding.
    with pytest.raises(HomeAssistantError, match="safety limit"):
        web._decode_content_encodings(gzip.compress(b"x" * 10000), "deflate, gzip", 100)


@pytest.mark.parametrize(
    "header", ["gzip,", "unknown", ",gzip", ",".join(["identity"] * 9)]
)
def test_invalid_or_excessive_encoding_stack_is_rejected(header):
    with pytest.raises(aiohttp.ClientPayloadError):
        web._decode_content_encodings(b"x", header, 1024)


@pytest.mark.parametrize("text", ["# retained comment", "# first\n\n# second\n"])
def test_comment_only_automation_file_is_empty_and_preserves_comments(tmp_path, text):
    path = tmp_path / "automations.yaml"
    path.write_text(text)
    config = {"id": "test", "alias": "Example", "triggers": [], "actions": []}
    previous, _, updated = native._append_automation_atomic(path, config)
    assert previous == text and updated.startswith(text)
    assert path.read_text() == updated
    assert yaml.safe_load(updated) == [config]


async def test_native_batch_reports_failure_then_continues(
    hass, exposed_entities, llm_context
):
    hass.services.async_call.side_effect = [HomeAssistantError("offline"), None]
    action = {
        "domain": "light",
        "service": "turn_on",
        "service_data": {"entity_id": ["light.living_room"]},
    }
    result = await native.NativeFunction().execute_service(
        hass, {}, {"list": [action, action]}, llm_context, exposed_entities
    )
    assert result == [{"error": "offline"}, {"success": True}]
    assert hass.services.async_call.await_count == 2
    hass.services.async_call.reset_mock(side_effect=True)
    hass.services.async_call.side_effect = HomeAssistantError("offline")
    with (
        propagate_function_execution_errors(),
        pytest.raises(HomeAssistantError, match="offline"),
    ):
        await native.NativeFunction().execute_service(
            hass, {}, {"list": [action, action]}, llm_context, exposed_entities
        )
    assert hass.services.async_call.await_count == 1


async def test_bounded_response_decodes_gzip_then_brotli():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    original = b"answer" * 100
    encoded = brotli.compress(gzip.compress(original))
    response = SimpleNamespace(
        status=200,
        content_length=len(encoded),
        headers={"Content-Encoding": "gzip, br"},
        content=SimpleNamespace(
            readexactly=AsyncMock(
                side_effect=asyncio.IncompleteReadError(encoded, 4097)
            )
        ),
    )
    assert await web._BoundedResponse(response, 4096).read() == original
