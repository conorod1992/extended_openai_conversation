"""Regression tests for authenticated and local execution security boundaries."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.function_groups import (
    assemble_function_tools,
)
from custom_components.extended_openai_conversation_responses.functions import BashFunction
from custom_components.extended_openai_conversation_responses.functions.sqlite import (
    _execute_sqlite_query,
    _read_only_sqlite_uri,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    async_call_ha_action,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    async_setup_ha_permissions,
    filter_entities_for_active_user,
    set_active_ha_context,
)


def test_bash_is_not_presented_without_explicit_shell_acknowledgement() -> None:
    """Legacy/default Bash definitions must be unavailable until explicitly opted in."""
    tool = {
        "spec": {"name": "bash", "description": "shell", "parameters": {}},
        "function": {"type": "bash", "command": "{{command}}"},
    }
    assert assemble_function_tools([tool], [], set()).tools == []

    tool["function"]["allow_unsafe_shell"] = True
    assert assemble_function_tools([tool], [], set()).tools == [tool]


async def test_bash_execution_requires_explicit_shell_acknowledgement(hass) -> None:
    """A direct/request-rule path cannot bypass the Bash opt-in boundary."""
    function = BashFunction()
    config = function.validate_schema({"type": "bash", "command": "echo safe"})

    result = await function.execute(hass, config, {}, None, [])

    assert "disabled" in result["error"].lower()


def test_bash_defensive_guard_catches_reordered_recursive_rm(tmp_path: Path) -> None:
    """The known rm -fr spelling is blocked as defence-in-depth."""
    function = BashFunction()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="recursive rm"):
        function._guard_command("rm -fr *", workspace, True)


async def test_authenticated_read_exposure_intersects_ha_permissions(hass) -> None:
    """Prompt/tool entity exposure must honor the authenticated user's READ policy."""
    user = MagicMock(id="restricted", is_active=True, is_admin=False)
    user.permissions.access_all_entities.return_value = False
    user.permissions.check_entity.side_effect = (
        lambda entity_id, policy: policy == POLICY_READ
        and entity_id == "light.allowed"
    )
    hass.auth.async_get_users.return_value = [user]
    await async_setup_ha_permissions(hass)
    set_active_ha_context(Context(user_id="restricted"))
    try:
        filtered = filter_entities_for_active_user(
            hass,
            [
                {"entity_id": "light.allowed"},
                {"entity_id": "light.secret"},
            ],
        )
    finally:
        set_active_ha_context(None)

    assert filtered == [{"entity_id": "light.allowed"}]
    user.permissions.check_entity.assert_any_call("light.allowed", POLICY_READ)
    user.permissions.check_entity.assert_any_call("light.secret", POLICY_READ)


async def test_ha_action_requires_control_permission_and_propagates_context(
    hass, monkeypatch
) -> None:
    """Resolved service targets require CONTROL and retain the originating Context."""
    context = Context(user_id="restricted")
    user = MagicMock(id="restricted", is_active=True, is_admin=False)
    user.permissions.check_entity.return_value = False
    hass.auth.async_get_user.return_value = user
    hass.services.has_service.return_value = True
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.ha_actions.target_helpers.async_extract_referenced_entity_ids",
        lambda *_args, **_kwargs: SimpleNamespace(
            referenced=set(), indirectly_referenced={"light.secret"}
        ),
    )
    set_active_ha_context(context)
    try:
        with pytest.raises(HomeAssistantError, match="does not have permission"):
            await async_call_ha_action(
                hass,
                "light",
                "turn_on",
                data={"area_id": ["private"]},
            )
        hass.services.async_call.assert_not_awaited()

        user.permissions.check_entity.return_value = True
        await async_call_ha_action(
            hass,
            "light",
            "turn_on",
            data={"area_id": ["private"]},
        )
    finally:
        set_active_ha_context(None)

    user.permissions.check_entity.assert_called_with("light.secret", POLICY_CONTROL)
    hass.services.async_call.assert_awaited_once()
    assert hass.services.async_call.await_args.kwargs["context"] is context


def _create_sqlite_fixture(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sample(value INTEGER)")
        conn.execute("INSERT INTO sample VALUES (42)")
        conn.commit()
    finally:
        conn.close()


def test_sqlite_plain_path_is_normalized_and_select_remains_available(
    tmp_path: Path,
) -> None:
    """Bare paths become real read-only file URIs without breaking SELECT."""
    db_path = tmp_path / "source.db"
    _create_sqlite_fixture(db_path)

    uri = _read_only_sqlite_uri(str(db_path))
    assert uri.startswith("file:")
    assert "mode=ro" in uri
    assert _execute_sqlite_query(uri, "SELECT value FROM sample", False, 10) == [
        {"value": 42}
    ]


def test_sqlite_authorizer_blocks_attach_and_vacuum_into(tmp_path: Path) -> None:
    """Read-only SQLite must not create files through statement side effects."""
    db_path = tmp_path / "source.db"
    _create_sqlite_fixture(db_path)
    uri = _read_only_sqlite_uri(str(db_path))

    attached = tmp_path / "attached.db"
    with pytest.raises(sqlite3.DatabaseError):
        _execute_sqlite_query(
            uri,
            f"ATTACH DATABASE '{attached}' AS attached",
            False,
            10,
        )
    assert not attached.exists()

    vacuumed = tmp_path / "vacuumed.db"
    with pytest.raises(sqlite3.DatabaseError):
        _execute_sqlite_query(
            uri,
            f"VACUUM INTO '{vacuumed}'",
            False,
            10,
        )
    assert not vacuumed.exists()
