"""A saved policy refresh uses the existing diagnostics without catalog loading."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from tests.test_save_validation_reuse import _hass_with_agent, warm


@pytest.mark.parametrize("is_admin", [False, True])
async def test_policy_projection_avoids_catalogue_loading(monkeypatch, is_admin):
    hass, entry, subentry = _hass_with_agent()
    _, revision = warm(subentry)
    guest = SimpleNamespace(status=lambda: {"currently_active": False})
    diagnostics = {"readable_entity_count": 7, "configured_tool_count": 1}
    policy = Mock(return_value=SimpleNamespace(as_diagnostics=lambda: diagnostics))
    knowledge = AsyncMock(side_effect=AssertionError("policy does not need Knowledge"))
    groups = Mock(side_effect=AssertionError("policy does not need the group catalogue"))
    exposed = Mock(return_value=[])
    monkeypatch.setattr(management_ui, "async_get_guest_mode", AsyncMock(return_value=guest))
    monkeypatch.setattr(management_ui, "async_get_knowledge", knowledge)
    monkeypatch.setattr(management_ui, "validate_function_groups", groups)
    monkeypatch.setattr(management_ui, "resolve_guest_policy", policy)
    monkeypatch.setattr(management_ui, "get_exposed_entities", exposed)
    request = management_ui._ManagementRequest(
        hass, "admin", is_admin, {"action": "policy"}, entry.entry_id,
        subentry.subentry_id, entry, subentry,
    )
    result = await management_ui.async_guest_mode_command(request)
    assert result == {"policy": diagnostics, "revision": revision}
    knowledge.assert_not_awaited()
    groups.assert_not_called()
    assert exposed.call_count == int(is_admin)
    assert ("exposed_entities" in policy.call_args.kwargs) is is_admin
