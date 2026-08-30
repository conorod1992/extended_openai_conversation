"""Regression tests for security and reliability boundary hardening."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.exceptions import (
    EntityNotExposed,
)
from custom_components.extended_openai_conversation_responses.functions import (
    BashFunction,
    NativeFunction,
    ReadFileFunction,
    TemplateFunction,
)
from custom_components.extended_openai_conversation_responses.services import (
    async_setup_services,
)


def test_file_same_prefix_sibling_is_not_allowed(hass, tmp_path: Path) -> None:
    """A same-prefix sibling must not satisfy an allowed-directory boundary."""
    function = ReadFileFunction()
    allowed = tmp_path / "extended_openai_conversation_responses"
    sibling = tmp_path / "extended_openai_conversation_responses_backup"
    sibling.mkdir()
    target = sibling / "secret.txt"
    target.write_text("secret")

    with pytest.raises(PermissionError):
        function._resolve_path(hass, str(target), [str(allowed)])


def test_bash_rejects_bare_parent_cd(tmp_path: Path) -> None:
    """Bare `cd ..` must not escape the configured working directory."""
    function = BashFunction()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="cd target"):
        function._guard_command("cd .. && pwd", workspace, True)


async def test_bash_custom_cwd_is_workspace_root(hass, tmp_path: Path) -> None:
    """A configured cwd remains a supported custom restricted workspace root."""
    function = BashFunction()
    custom_workspace = tmp_path / "custom_workspace"
    custom_workspace.mkdir()
    config = function.validate_schema(
        {
            "type": "bash",
            "command": "pwd",
            "cwd": str(custom_workspace),
            "restrict_to_workspace": True,
        }
    )

    result = await function.execute(hass, config, {}, None, [])

    assert result["exit_code"] == 0
    assert Path(result["stdout"].strip()).resolve() == custom_workspace.resolve()


async def test_template_entity_ids_must_be_exposed(hass) -> None:
    """Template tools must not bypass the exposed-entity policy."""
    function = TemplateFunction()
    config = function.validate_schema(
        {
            "type": "template",
            "value_template": "{{ states[entity_id[0]].state }}",
        }
    )

    with pytest.raises(EntityNotExposed):
        await function.execute(
            hass,
            config,
            {"entity_id": ["light.secret"]},
            None,
            [{"entity_id": "light.living_room"}],
        )


async def test_native_indirect_target_must_be_exposed(hass, monkeypatch) -> None:
    """Area/device targets must be authorized after resolving to entities."""
    function = NativeFunction()
    hass.states.get = MagicMock(return_value=MagicMock(state="off"))
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.native.target_helpers.async_extract_referenced_entity_ids",
        lambda *_args, **_kwargs: SimpleNamespace(
            referenced=set(), indirectly_referenced={"light.secret"}
        ),
    )

    with pytest.raises(EntityNotExposed):
        await function.execute_service_single(
            hass,
            {"type": "native", "name": "execute_service_single"},
            {
                "domain": "light",
                "service": "turn_on",
                "service_data": {"area_id": ["private_area"]},
            },
            None,
            [{"entity_id": "light.living_room"}],
        )

    hass.services.async_call.assert_not_awaited()


async def _service_handlers(hass) -> dict[str, object]:
    await async_setup_services(hass, {})
    return {
        call.args[1]: call.args[2]
        for call in hass.services.async_register.call_args_list
    }


async def test_change_config_requires_admin(hass) -> None:
    """Credential/provider mutation must reject a non-admin service caller."""
    handlers = await _service_handlers(hass)
    hass.auth.async_get_user.return_value = MagicMock(is_admin=False)
    call = SimpleNamespace(
        context=Context(user_id="non_admin"),
        data={"config_entry": "entry", "api_key": "should-not-be-used"},
    )

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await handlers["change_config"](call)


async def test_query_image_requires_admin(hass) -> None:
    """A user-triggered billed image query must be admin-only."""
    handlers = await _service_handlers(hass)
    hass.auth.async_get_user.return_value = MagicMock(is_admin=False)
    call = SimpleNamespace(context=Context(user_id="non_admin"), data={})

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await handlers["query_image"](call)


async def test_download_skill_rejects_path_like_name(hass) -> None:
    """Skill names must not be usable as local filesystem traversal paths."""
    handlers = await _service_handlers(hass)
    hass.auth.async_get_user.return_value = MagicMock(is_admin=True)
    call = SimpleNamespace(
        context=Context(user_id="admin"), data={"skill_name": "../outside"}
    )

    with pytest.raises(HomeAssistantError, match="letters, numbers"):
        await handlers["download_skill"](call)
