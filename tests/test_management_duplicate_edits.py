"""Duplicate confirmations use authenticated, revision-checked Management edits."""

from datetime import timedelta

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SHARED_MEMORY_MODE,
    SHARED_MEMORY_EXPLICIT,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util


async def test_knowledge_warns_only_for_new_content_collision(hass, management_message):
    async def command(action, **fields):
        return await async_management_command(
            hass, "admin", True, management_message("knowledge", action, **fields)
        )

    first = (await command("create", title="First", content="Original"))["source"]
    second = (await command("create", title="Second", content="Existing"))["source"]
    assert await command(
        "create",
        title="Alias",
        description="Other description",
        content="Existing",
        confirm_duplicate=False,
    ) == {"status": "duplicate"}

    edit = {
        "source_id": first["source_id"],
        "expected_revision": first["updated_at"],
        "content": "Existing\n",
    }
    assert await command("update", **edit, confirm_duplicate=False) == {
        "status": "duplicate"
    }
    assert (await command("get", source_id=first["source_id"]))["source"] == first

    changed = (
        await command("update", source_id=first["source_id"], title="Concurrent title")
    )["source"]
    with pytest.raises(ValueError, match="changed in another tab"):
        await command("update", **edit, confirm_duplicate=True)
    edit["expected_revision"] = changed["updated_at"]
    assert await command("update", **edit, confirm_duplicate=False) == {
        "status": "duplicate"
    }
    saved = (await command("update", **edit, confirm_duplicate=True))["source"]
    assert saved["source_id"] == first["source_id"]
    assert saved["content"] == second["content"]
    assert (await command("get", source_id=second["source_id"]))["source"] == second

    renamed = await command(
        "update",
        source_id=saved["source_id"],
        expected_revision=saved["updated_at"],
        description="Useful under a different heading",
        enabled=False,
        confirm_duplicate=False,
    )
    assert renamed["status"] == "updated"
    assert renamed["source"]["enabled"] is False
    assert (
        await command(
            "update",
            source_id=second["source_id"],
            content="Only this source has it",
            confirm_duplicate=False,
        )
    )["status"] == "updated"


async def test_persistent_move_warns_only_in_authorized_destination(
    hass, management_agent, management_message
):
    _, agent = management_agent
    agent.data[CONF_SHARED_MEMORY_MODE] = SHARED_MEMORY_EXPLICIT
    personal = "user:admin"
    shared = "shared:household"

    async def command(action, *, admin=True, **fields):
        return await async_management_command(
            hass, "admin", admin, management_message("memories", action, **fields)
        )

    first = (await command("add", scope_id=personal, content="A fact"))["memory"]
    second = (await command("add", scope_id=shared, content="A fact"))["memory"]
    edit = {
        "scope_id": personal,
        "target_scope_id": shared,
        "memory_id": first["memory_id"],
        "expected_revision": first["revision"],
    }
    with pytest.raises(HomeAssistantError, match="scope is not available"):
        await command("update", **edit, admin=False, confirm_duplicate=False)
    assert await command("update", **edit, confirm_duplicate=False) == {
        "status": "duplicate"
    }
    assert [
        row["memory_id"]
        for row in (await command("list", scope_id=personal))["memories"]
    ] == [first["memory_id"]]

    moved = await command("update", **edit, confirm_duplicate=True)
    assert moved["scope_id"] == shared
    assert moved["memory"]["memory_id"] == first["memory_id"]
    assert {
        row["memory_id"] for row in (await command("list", scope_id=shared))["memories"]
    } == {first["memory_id"], second["memory_id"]}


async def test_temporary_edit_confirms_same_owned_fact_and_rechecks_revision(
    hass, management_message
):
    expiry = (dt_util.utcnow() + timedelta(hours=1)).isoformat()

    async def command(action, *, scope="user:admin", **fields):
        return await async_management_command(
            hass,
            "admin",
            True,
            management_message("memories", action, scope_id=scope, **fields),
        )

    first = (await command("temporary_add", content="Original", expires_at=expiry))[
        "memory"
    ]
    second = (await command("temporary_add", content="Existing", expires_at=expiry))[
        "memory"
    ]
    await command(
        "temporary_add", scope="user:other", content="Other owner", expires_at=expiry
    )
    edit = {
        "memory_id": first["memory_id"],
        "expected_revision": first["updated_at"],
        "content": "Existing",
    }
    assert await command("temporary_update", **edit, confirm_duplicate=False) == {
        "status": "duplicate"
    }
    # A fact in another owner's collection does not warn in this one.
    changed = (
        await command(
            "temporary_update",
            memory_id=first["memory_id"],
            content="Other owner",
            confirm_duplicate=False,
        )
    )["memory"]
    with pytest.raises(HomeAssistantError, match="changed since it was loaded"):
        await command("temporary_update", **edit, confirm_duplicate=True)
    edit["expected_revision"] = changed["updated_at"]
    saved = await command("temporary_update", **edit, confirm_duplicate=True)
    assert saved["memory"]["content"] == second["content"]
    assert {
        row["memory_id"] for row in (await command("temporary_list"))["memories"]
    } == {first["memory_id"], second["memory_id"]}
