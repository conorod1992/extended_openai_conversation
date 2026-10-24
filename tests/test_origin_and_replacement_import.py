"""Invocation origin and replacement import recovery regression boundaries."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL,
    redact_secrets,
)
from tests.test_management_request_boundaries import _tool


def damaged_configuration(kind):
    healthy = _tool("healthy")
    broken = _tool("legacy_reminder")
    broken["function"] = {"type": "native", "name": "reminders.unavailable"}
    return {
        "functions": [healthy, broken] if kind == "tools" else [healthy],
        "function_groups": []
        if kind == "tools"
        else [
            {
                "id": "damaged",
                "name": "Damaged",
                "description": "Legacy",
                "functions": ["missing_tool"],
                "loading_mode": "always",
            }
        ],
    }


def import_document(config=None):
    return {
        "schema": "extended_openai_conversation.agent",
        "version": 1,
        "title": "Repaired import",
        "config": {"functions": [], "function_groups": [], **(config or {})},
    }


@pytest.mark.parametrize("kind", ["tools", "groups"])
def test_valid_replacement_does_not_require_valid_destination(hass, kind):
    original = damaged_configuration(kind)
    destination = SimpleNamespace(data=deepcopy(original))
    parsed = management_ui._parse_import_document(
        import_document(), destination=destination
    )
    assert yaml.safe_load(parsed["config"]["functions"]) == []
    assert parsed["config"]["function_groups"] == []
    assert destination.data == original


@pytest.mark.parametrize("kind", ["tools", "groups"])
@pytest.mark.parametrize("changed_destination", [False, True])
def test_import_quarantine_preserves_secret_destination_binding(
    hass, kind, changed_destination
):
    rest = _tool("private_rest")
    rest["function"] = {
        "type": "rest",
        "resource": "https://safe.test/api",
        "headers": {"Authorization": "Bearer local-credential"},
    }
    original = damaged_configuration(kind)
    original["functions"].append(rest)
    replacement = redact_secrets({"functions": [rest], "function_groups": []})
    if changed_destination:
        replacement["functions"][0]["function"]["resource"] = (
            "https://different.test/api"
        )
        with pytest.raises(management_ui.AgentConfigError, match="unavailable secrets"):
            management_ui._parse_import_document(
                import_document(replacement), destination=SimpleNamespace(data=original)
            )
    else:
        parsed = management_ui._parse_import_document(
            import_document(replacement), destination=SimpleNamespace(data=original)
        )
        assert (
            yaml.safe_load(parsed["config"]["functions"])[0]["function"]["headers"][
                "Authorization"
            ]
            == "Bearer local-credential"
        )


async def test_model_broadcast_carries_satellite_without_device(hass, monkeypatch):
    from tests.test_conversation_orchestration import _assist_fixture

    entity, invoke, _logs, _seen = _assist_fixture(monkeypatch)
    manager = SimpleNamespace(
        resolve_targets=lambda **kw: (
            ["assist_satellite.hall"]
            if kw.get("origin_entity_id") == "assist_satellite.kitchen"
            else ["assist_satellite.hall", "assist_satellite.kitchen"]
        ),
        async_send=AsyncMock(
            return_value={
                "id": "test",
                "targets": ["assist_satellite.hall"],
                "deliveries": {},
            }
        ),
    )
    monkeypatch.setattr(native, "async_get_intercom", AsyncMock(return_value=manager))

    async def process(_log, **kwargs):
        await native.NativeFunction().send_broadcast(
            hass,
            {},
            {"whole_home": True, "message": "Dinner"},
            SimpleNamespace(device_id=None),
            [],
        )
        return False

    entity._async_handle_chat_log.side_effect = process
    await invoke(satellite="assist_satellite.kitchen", device=None)
    assert manager.async_send.call_args.kwargs["entity_ids"] == [
        "assist_satellite.hall"
    ]
    assert (
        manager.async_send.call_args.kwargs["origin_entity_id"]
        == "assist_satellite.kitchen"
    )


@pytest.mark.parametrize(
    "failure", [None, RuntimeError, __import__("asyncio").CancelledError]
)
@pytest.mark.parametrize("satellite", [None, "assist_satellite.child"])
async def test_invocation_origin_restores_caller_on_all_exits(
    hass, monkeypatch, failure, satellite
):
    from custom_components.extended_openai_conversation_responses.request_origin import (
        bind_request_satellite,
        get_request_satellite,
    )
    from tests.test_conversation_orchestration import _assist_fixture

    entity, invoke, _logs, _seen = _assist_fixture(monkeypatch)

    async def boundary(_request):
        assert get_request_satellite() == satellite
        if failure:
            raise failure("stopped")
        return "done"

    entity._async_process_with_continuity = boundary
    with bind_request_satellite("assist_satellite.parent"):
        if failure:
            with pytest.raises(failure):
                await invoke(satellite=satellite)
        else:
            assert await invoke(satellite=satellite) == "done"
        assert get_request_satellite() == "assist_satellite.parent"
    assert get_request_satellite() is None


def test_import_with_unavailable_required_secret_still_fails(hass):
    rest = _tool("missing_secret")
    rest["function"] = {
        "type": "rest",
        "resource": "https://safe.test/api",
        "headers": {"Authorization": REDACTED_SECRET_SENTINEL},
    }
    with pytest.raises(management_ui.AgentConfigError, match="unavailable secrets"):
        management_ui._parse_import_document(
            import_document({"functions": [rest]}),
            destination=SimpleNamespace(data=damaged_configuration("tools")),
        )


def test_plain_import_does_not_decode_unreadable_destination(hass):
    destination = SimpleNamespace(data={"functions": "[{unclosed"})
    parsed = management_ui._parse_import_document(
        import_document(), destination=destination
    )
    assert yaml.safe_load(parsed["config"]["functions"]) == []
