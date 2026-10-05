"""Realistic remaining input, response, catalogue, and cleanup boundaries."""

import asyncio
from copy import deepcopy
import gzip
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import zlib

import aiohttp
import pytest

from custom_components.extended_openai_conversation_responses import (
    ha_permissions as permissions,
    management_ui as ui,
    model_catalog as catalog,
    model_catalog_manager as models,
    regex_execution,
)
from custom_components.extended_openai_conversation_responses.functions import bash, web
from homeassistant.exceptions import HomeAssistantError
from tests.test_bash_function_coverage import _process
from tests.test_remaining_management_contracts import _request
from tests.test_service_handlers import _call, _download_handler


@pytest.mark.parametrize(
    "encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)]
)
@pytest.mark.parametrize(
    "body,expected",
    [(b"hello world", "hello world"), ("caf\u00e9".encode(), "caf\u00e9")],
)
async def test_bounded_response_decodes_and_caches_compressed_body(
    encoding, compress, body, expected
):
    compressed = compress(body)
    read = AsyncMock(side_effect=asyncio.IncompleteReadError(compressed, 129))
    response = SimpleNamespace(
        status=200,
        content_length=len(compressed),
        headers={"Content-Encoding": f" {encoding.upper()} "},
        content=SimpleNamespace(readexactly=read),
        charset="utf-8",
    )
    bounded = web._BoundedResponse(response, 128)
    assert await bounded.read() == body
    assert await bounded.text() == expected
    assert await bounded.read() == body
    read.assert_awaited_once_with(129)


@pytest.mark.parametrize("failure", ["expansion", "corrupt"])
async def test_bounded_response_rejects_unsafe_compression_without_caching(failure):
    body = gzip.compress(b"x" * 10000) if failure == "expansion" else b"invalid frame"
    response = SimpleNamespace(
        status=200,
        content_length=len(body),
        headers={"Content-Encoding": "gzip"},
        content=SimpleNamespace(
            readexactly=AsyncMock(side_effect=asyncio.IncompleteReadError(body, 129))
        ),
    )
    bounded = web._BoundedResponse(response, 128)
    with pytest.raises((HomeAssistantError, aiohttp.ClientPayloadError)):
        await bounded.read()
    assert bounded._body is None


async def test_skill_download_rejects_symlink_destination_before_network(
    hass, monkeypatch, tmp_path
):
    handler, manager = await _download_handler(hass, monkeypatch, tmp_path, {})
    manager.user_skills_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("private")
    try:
        (manager.user_skills_dir / "demo").symlink_to(outside, target_is_directory=True)
    except OSError as err:
        pytest.skip(f"Directory symlinks unavailable on this platform: {err}")
    with pytest.raises(HomeAssistantError, match="target directory is unsafe"):
        await handler(_call({"skill_name": "demo"}))
    assert sentinel.read_text() == "private"
    manager.async_publish_staged_skill.assert_not_awaited()
    assert not manager.staging_dir.exists()


@pytest.mark.parametrize("parent", ["", "invalid parent", 42, []])
def test_catalog_rejects_invalid_snapshot_parent(parent):
    candidate = deepcopy(catalog.BUNDLED_CATALOG)
    model = deepcopy(candidate["models"][0])
    model.update(id="invalid-snapshot", kind="snapshot", alias_of=parent)
    candidate["models"].append(model)
    with pytest.raises(ValueError, match="Invalid snapshot parent"):
        catalog.validate_catalog(candidate)


@pytest.mark.parametrize(
    "field,value", [("model", "gpt-4.1"), ("reasoning_effort", "low")]
)
async def test_catalog_checks_single_field_routing_without_losing_other_options(
    hass, monkeypatch, field, value
):
    from custom_components.extended_openai_conversation_responses import request

    options = {models.CONF_CHAT_MODEL: "gpt-5", models.CONF_REASONING_EFFORT: "medium"}
    entry = SimpleNamespace(
        entry_id="entry",
        data={},
        subentries={
            "agent": SimpleNamespace(
                subentry_id="agent", subentry_type="conversation", data=options
            ),
            "other": SimpleNamespace(subentry_type="other", data={}),
        },
    )
    hass.config_entries.async_entries.return_value = [entry]
    monkeypatch.setattr(
        models,
        "async_get_request_rules",
        AsyncMock(
            return_value=SimpleNamespace(
                snapshot=lambda: {
                    "rules": [
                        {"action_type": "model_routing", "action": {field: value}}
                    ]
                }
            )
        ),
    )
    build = Mock()
    monkeypatch.setattr(request, "build_provider_request_snapshot", build)
    manager = models.ModelCatalogManager(hass)
    assert await manager._candidate_preserves_saved_requests(catalog.BUNDLED_CATALOG)
    assert build.call_count == 2
    effective = build.call_args.args[0]
    key = models.CONF_CHAT_MODEL if field == "model" else models.CONF_REASONING_EFFORT
    assert effective == {**options, key: value}
    assert options == {
        models.CONF_CHAT_MODEL: "gpt-5",
        models.CONF_REASONING_EFFORT: "medium",
    }


async def test_catalog_websocket_successful_apply_returns_capabilities(hass):
    status = {"last_error": None, "source": "updated", "catalog_version": 999}
    manager = SimpleNamespace(
        async_apply_update=AsyncMock(return_value=status),
        status=lambda: status,
        catalog=None,
    )
    hass.data[models.DATA_MANAGER] = manager
    connection = SimpleNamespace(send_result=Mock(), send_error=Mock())
    handler = inspect.unwrap(models.websocket_catalog)
    await handler(hass, connection, {"id": 1, "action": "apply", "model": "gpt-4.1"})
    manager.async_apply_update.assert_awaited_once()
    connection.send_error.assert_not_called()
    result = connection.send_result.call_args.args[1]
    assert result["source"] == "updated" and result["catalog_version"] == 999
    assert "model_capabilities" in result


@pytest.mark.parametrize("value", ["true", 1, None, [], {}])
async def test_knowledge_setting_rejects_nonboolean_without_persisting(
    hass, monkeypatch, value
):
    library = SimpleNamespace()
    monkeypatch.setattr(ui, "async_get_knowledge", AsyncMock(return_value=library))
    with pytest.raises(HomeAssistantError, match="enabled must be a boolean"):
        await ui.async_knowledge_command(
            _request(hass, "knowledge", "set_enabled", enabled=value)
        )
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("graceful", [False, True])
async def test_nonposix_subprocess_cleanup_does_not_signal_exited_process(
    monkeypatch, graceful
):
    process = _process(returncode=0)
    readers = [
        asyncio.create_task(asyncio.sleep(0, result=(b"out", False))) for _ in range(2)
    ]
    monkeypatch.setattr(bash.os, "name", "nt")
    await bash._async_cleanup_process(process, *readers, graceful=graceful)
    process.kill.assert_not_called()
    process.terminate.assert_not_called()
    process.wait.assert_not_awaited()
    assert all(task.done() for task in readers)


async def test_regex_cleanup_reaps_already_exited_worker():
    process = SimpleNamespace(returncode=0, kill=Mock(), wait=AsyncMock(return_value=0))
    await regex_execution._async_stop_regex_worker(process)
    process.kill.assert_not_called()
    process.wait.assert_awaited_once()


def test_read_permission_success_reports_exposure_policy_instead_of_user_denial():
    check = Mock(return_value=True)
    user = SimpleNamespace(
        is_active=True, permissions=SimpleNamespace(check_entity=check)
    )
    hass = SimpleNamespace(data={permissions._USER_CACHE_KEY: {"alice": user}})
    with permissions.bind_active_ha_context(SimpleNamespace(user_id="alice")):
        error = permissions.entity_access_error(hass, ["light.kitchen"])
    assert error.reason == "policy"
    check.assert_called_once_with("light.kitchen", permissions.POLICY_READ)
