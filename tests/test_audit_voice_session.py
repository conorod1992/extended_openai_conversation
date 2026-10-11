"""Voice fallback and runtime-generation cleanup regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
    request_rule_session_id,
)
from custom_components.extended_openai_conversation_responses.scope import (
    UNRETAINED_SCOPE_ID,
    resolve_data_scope,
)
from custom_components.extended_openai_conversation_responses.voice_identity_runtime import (
    voice_identity_scope,
)


@pytest.mark.parametrize(
    "device,mappings",
    [(None, {}), (None, []), ("unknown", {}), ("kitchen", {"kitchen": "user:deleted"})],
)
@pytest.mark.parametrize(
    "fallback,expected",
    [("shared", "shared:household"), ("default_user", "user:alice")],
)
async def test_unselectable_owner_uses_validated_fallback(
    device, mappings, fallback, expected
):
    options = {
        "voice_scope_policy": "device_mapping",
        "voice_device_mappings": mappings,
        "voice_unmapped_policy": fallback,
        "voice_default_user_id": "user:alice",
    }
    lookup = AsyncMock(
        side_effect=lambda uid: (
            SimpleNamespace(is_active=True) if uid == "alice" else None
        )
    )
    agent = SimpleNamespace(
        subentry=SimpleNamespace(data=options),
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=lookup)),
    )
    request = SimpleNamespace(
        device_id=device, satellite_id=None, context=SimpleNamespace(user_id=None)
    )
    async with voice_identity_scope(agent, request):
        assert resolve_data_scope(request, options).scope_id == expected
    assert all(call.args[0] != "user:alice" for call in lookup.await_args_list)


async def test_inactive_default_stays_unretained_and_explicit_assignment_wins():
    options = {
        "voice_scope_policy": "device_mapping",
        "voice_unmapped_policy": "default_user",
        "voice_default_user_id": "user:alice",
        "voice_device_mappings": {},
    }
    lookup = AsyncMock(return_value=SimpleNamespace(is_active=False))
    agent = SimpleNamespace(
        subentry=SimpleNamespace(data=options),
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=lookup)),
    )
    request = SimpleNamespace(
        device_id=None, satellite_id=None, context=SimpleNamespace(user_id=None)
    )
    async with voice_identity_scope(agent, request):
        assert resolve_data_scope(request, options).scope_id == UNRETAINED_SCOPE_ID
    options["voice_device_mappings"] = {"kitchen": UNRETAINED_SCOPE_ID}
    request.device_id = "kitchen"
    lookup.reset_mock()
    async with voice_identity_scope(agent, request):
        assert resolve_data_scope(request, options).scope_id == UNRETAINED_SCOPE_ID
    lookup.assert_not_awaited()


def test_ending_continuity_invalidates_generations_without_touching_other_owners():
    rules = RequestRuleRuntime()
    groups = FunctionGroupRuntime()
    own = [request_rule_session_id("owner", id) for id in ("old", "new")]
    other = request_rule_session_id("owner-longer", "other")
    tokens = []
    for key in [*own, other]:
        rules.set(key, {"chat_model": "gpt-4.1"})
        tokens.append(rules.begin_request(key, 5))
        groups.begin(key, 5)
    rules.reset_continuity("owner")
    groups.end_continuity("owner")
    assert all(rules.get(key) == {} for key in own)
    assert rules.get(other) == {"chat_model": "gpt-4.1"}
    assert set(groups._sessions) == {other}
    assert list(rules._requests.values()) == [other]
