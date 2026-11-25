"""Service validation must preserve concurrent agent and rule changes."""

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
from tests.test_service_handlers import _call, _handlers


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

    def validate(*_args, ha_snapshot, reserve_ha_tools):
        assert ha_snapshot is None
        assert reserve_ha_tools is False
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
    from homeassistant.config_entries import ConfigEntryState

    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED,
        disabled_by=None,
        domain=DOMAIN,
        entry_id="entry",
        data={CONF_API_KEY: "old"},
        subentries=children,
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
        enabled,
        children["conversation"].data,
        {CONF_API_KEY: "new"},
        ha_snapshot=None,
        reserve_ha_tools=False,
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
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request.normalize_output_token_limit",
        lambda *_: None,
    )
    handler = (await _handlers(hass))["query_image"]
    with pytest.raises(HomeAssistantError, match="Output token limit is required"):
        await handler(
            _call({"model": "model", CONF_API_MODE: "responses", "max_tokens": None})
        )
    hass.async_add_executor_job.assert_not_awaited()
    hass.config_entries.async_get_entry.assert_not_called()


@pytest.mark.parametrize("value", [False, 0, "", None, {"result": {"level": 62}}])
async def test_function_service_wraps_the_complete_native_value(hass, monkeypatch, value):
    execute = AsyncMock(return_value=value)
    monkeypatch.setattr(services, "async_call_active_function", execute)
    handler = (await _handlers(hass))["call_function"]

    result = await handler(_call({"function": "read", "arguments": {"room": "kitchen"}}))

    execute.assert_awaited_once_with("read", {"room": "kitchen"})
    assert result == {"result": value}
    assert result["result"] is value
