"""Service validation must preserve concurrent agent and rule changes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    request,
    request_rules as rr,
    services,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    DOMAIN,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError
from tests.test_service_handlers import _call, _download_handler, _handlers, _Response


@pytest.mark.parametrize(
    "change,message",
    [
        ("data", "Agent configuration changed"),
        ("removed", "Agent configuration changed"),
        ("rules", "Request Rules changed"),
    ],
)
async def test_provider_change_rejects_concurrent_child_edits(
    hass, monkeypatch, change, message
):
    child = SimpleNamespace(
        subentry_id="agent", subentry_type="conversation", title="Agent", data={}
    )
    entry = SimpleNamespace(
        domain=DOMAIN,
        entry_id="entry",
        data={CONF_API_KEY: "old"},
        subentries={"agent": child},
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        services, "get_authenticated_client", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(rr, "validate_routed_request_options", Mock())
    revision = ["before"]
    rules = SimpleNamespace(
        revision=lambda: revision[0], snapshot=lambda: {"rules": [{"enabled": True}]}
    )
    monkeypatch.setattr(rr, "async_get_request_rules", AsyncMock(return_value=rules))

    def validate(*_args):
        if change == "data":
            child.data = {"prompt": "changed elsewhere"}
        elif change == "removed":
            entry.subentries.clear()
        else:
            revision[0] = "after"

    monkeypatch.setattr(rr, "validate_rule_model_request", validate)
    handler = (await _handlers(hass))["change_config"]
    with pytest.raises(HomeAssistantError, match=message):
        await handler(_call({"config_entry": "entry", CONF_API_KEY: "new"}))
    hass.config_entries.async_update_entry.assert_not_called()
    assert entry.data[CONF_API_KEY] == "old"


async def test_provider_change_validates_only_enabled_rules_and_llm_subentries(
    hass, monkeypatch
):
    children = {
        kind: SimpleNamespace(
            subentry_id=kind, subentry_type=kind, title=kind, data={"kind": kind}
        )
        for kind in ["conversation", "ai_task_data", "other"]
    }
    entry = SimpleNamespace(
        domain=DOMAIN, entry_id="entry", data={CONF_API_KEY: "old"}, subentries=children
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        services, "get_authenticated_client", AsyncMock(return_value=object())
    )
    routed, snapshot, validate = Mock(), Mock(), Mock()
    monkeypatch.setattr(rr, "validate_routed_request_options", routed)
    monkeypatch.setattr(request, "build_provider_request_snapshot", snapshot)
    monkeypatch.setattr(rr, "validate_rule_model_request", validate)
    enabled = {"id": "enabled", "enabled": True}
    monkeypatch.setattr(
        rr,
        "async_get_request_rules",
        AsyncMock(
            return_value=SimpleNamespace(
                revision=lambda: "same",
                snapshot=lambda: {
                    "rules": [enabled, {"id": "disabled", "enabled": False}]
                },
            )
        ),
    )
    await (await _handlers(hass))["change_config"](
        _call({"config_entry": "entry", CONF_API_KEY: "new"})
    )
    validate.assert_called_once_with(
        enabled, children["conversation"].data, {CONF_API_KEY: "new"}
    )
    routed.assert_called_once()
    snapshot.assert_called_once_with(
        children["ai_task_data"].data, {CONF_API_KEY: "new"}, tools_required=False
    )
    assert hass.config_entries.async_update_entry.call_args.kwargs["data"] == {
        CONF_API_KEY: "new"
    }


async def test_query_image_requires_token_limit_before_reading_files(hass, monkeypatch):
    monkeypatch.setattr(services, "get_api_mode", lambda *_: "responses")
    monkeypatch.setattr(services, "normalize_output_token_limit", lambda *_: None)
    handler = (await _handlers(hass))["query_image"]
    with pytest.raises(HomeAssistantError, match="Output token limit is required"):
        await handler(
            _call({"model": "model", CONF_API_MODE: "responses", "max_tokens": None})
        )
    hass.async_add_executor_job.assert_not_awaited()
    hass.config_entries.async_get_entry.assert_not_called()


async def test_function_service_forwards_result_alias_and_unwraps_response(
    hass, monkeypatch
):
    execute = AsyncMock(return_value={"status": "ok", "value": 7})
    monkeypatch.setattr(services, "async_call_active_function", execute)
    handler = (await _handlers(hass))["call_function"]
    result = await handler(
        _call(
            {
                "function": "read",
                "arguments": {"room": "kitchen"},
                "result_alias": "reading",
            }
        )
    )
    execute.assert_awaited_once_with("read", {"room": "kitchen"}, "reading")
    assert result == {"result": {"status": "ok", "value": 7}}


@pytest.mark.parametrize("failure", ["cancel", "write-error", "executor-cancelled"])
async def test_download_cancellation_settles_writer_before_staging_cleanup(
    hass, tmp_path, monkeypatch, failure
):
    url = "https://api.github.com/repos/conorod1992/extended_openai_conversation/contents/examples/skills/demo?ref=v1.2.3"
    responses = {
        url: _Response(
            200,
            payload=[
                {
                    "name": "SKILL.md",
                    "path": "SKILL.md",
                    "type": "file",
                    "download_url": "download:skill",
                    "size": 7,
                }
            ],
        ),
        "download:skill": _Response(200, body=b"# Demo\n"),
    }
    handler, manager = await _download_handler(hass, monkeypatch, tmp_path, responses)
    entered, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(function, *args):
        if function.__name__ == "_write_file_sync":
            entered.set()
            if failure != "executor-cancelled":
                await release.wait()
            settled.set()
            if failure == "write-error":
                raise OSError("write failed")
            if failure == "executor-cancelled":
                raise asyncio.CancelledError()
        if function.__name__ == "_cleanup_path":
            assert settled.is_set(), "cleanup must not race an unfinished native writer"
        return function(*args)

    hass.async_add_executor_job = execute
    task = asyncio.create_task(handler(_call({"skill_name": "demo"})))
    await asyncio.wait_for(entered.wait(), 2)
    if failure != "executor-cancelled":
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
    expected = (
        HomeAssistantError if failure == "write-error" else asyncio.CancelledError
    )
    with pytest.raises(expected):
        await task
    assert not (tmp_path / "staging" / "demo.download-fixed").exists()
    manager.async_publish_staged_skill.assert_not_awaited()


async def test_skill_download_skips_non_file_directory_entries(
    hass, tmp_path, monkeypatch
):
    url = "https://api.github.com/repos/conorod1992/extended_openai_conversation/contents/examples/skills/demo?ref=v1.2.3"
    handler, manager = await _download_handler(
        hass,
        monkeypatch,
        tmp_path,
        {
            url: _Response(
                200,
                payload=[
                    {"name": "link", "type": "symlink"},
                    {
                        "name": "SKILL.md",
                        "type": "file",
                        "path": "SKILL.md",
                        "download_url": "file",
                        "size": 7,
                    },
                ],
            ),
            "file": _Response(200, body=b"# Demo\n"),
        },
    )
    result = await handler(_call({"skill_name": "demo"}))
    assert result["downloaded_files"] == ["SKILL.md"]
    assert not (tmp_path / "staging" / "demo.download-fixed" / "link").exists()
    manager.async_publish_staged_skill.assert_awaited_once()
