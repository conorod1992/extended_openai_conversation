"""Final setup ownership, compatibility and dead-code regression boundaries."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    agent_config,
    debug_ui,
    frontend_assets,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_EXPOSED_ENTITY_ATTRIBUTES,
    CONF_FUNCTION_TOOLS,
)

ROOT = Path(integration.__file__).parent


def test_owned_functions_have_no_runtime_replacement():
    protected = {
        "async_setup_management_ui",
        "async_setup_debug_ui",
        "to_openapi",
        "normalize_agent_config",
        "AGENT_CONFIG_DEFAULTS",
        "AGENT_CONFIG_FIELDS",
        "configured_function_tools_from_data",
        "validate_function_groups",
        "_async_handle_message",
        "_transform_chat_stream",
        "summary",
        "function_references",
        "async_rename_function_reference",
        "_async_retry_agent",
    }
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Delete)):
                targets = (
                    [node.target] if isinstance(node, ast.AnnAssign) else node.targets
                )
                for target in targets:
                    assert not (
                        isinstance(target, ast.Attribute) and target.attr in protected
                    ), (path, node.lineno)
                    assert not (
                        isinstance(target, ast.Name) and target.id == "_INSTALLED"
                    ), path
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"setattr", "delattr"}
            ):
                assert not (
                    len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in protected
                ), path
            if isinstance(node, ast.Attribute):
                assert not (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "sys"
                    and node.attr == "modules"
                ), path
    assert (
        integration.async_setup_management_ui is management_ui.async_setup_management_ui
    )
    assert integration.async_setup_debug_ui is debug_ui.async_setup_debug_ui


async def test_setup_retries_asset_failure_and_preserves_identities(monkeypatch):
    hass = SimpleNamespace(data={})
    register = AsyncMock(side_effect=[RuntimeError("asset failure"), None])
    monkeypatch.setattr(management_ui, "async_register_frontend_assets", register)
    monkeypatch.setattr(
        management_ui, "frontend_entry_url", lambda *_: "/frontend/management-test.js"
    )
    monkeypatch.setattr(management_ui.websocket_api, "async_register_command", Mock())
    monkeypatch.setattr(management_ui.panel_custom, "async_register_panel", AsyncMock())
    setup = management_ui.async_setup_management_ui
    with pytest.raises(RuntimeError, match="asset failure"):
        await setup(hass)
    assert not hass.data
    await setup(hass)
    await setup(hass)
    assert register.await_count == 2
    assert management_ui.async_setup_management_ui is setup
    assert integration.async_setup_management_ui is setup


async def test_shared_versioned_assets_retry_and_register_once(monkeypatch):
    register = AsyncMock(side_effect=[RuntimeError("HTTP unavailable"), None])
    hass = SimpleNamespace(
        data={},
        http=SimpleNamespace(async_register_static_paths=register),
        async_add_executor_job=AsyncMock(side_effect=lambda callback: callback()),
    )
    with pytest.raises(RuntimeError, match="HTTP unavailable"):
        await frontend_assets.async_register_frontend_assets(hass)
    assert not hass.data
    await frontend_assets.async_register_frontend_assets(hass)
    await frontend_assets.async_register_frontend_assets(hass)
    assert register.await_count == 2
    assert hass.async_add_executor_job.await_count == 2
    asset = register.await_args.args[0][0]
    assert asset.cache_headers is True
    assert frontend_assets.frontend_entry_url(hass, "management").startswith(
        asset.url_path + "/assets/management-"
    )


async def test_exposed_attribute_contract_is_owned_by_config():
    defaults = agent_config.agent_config_defaults()
    defaults[CONF_FUNCTION_TOOLS] = []
    defaults[CONF_EXPOSED_ENTITY_ATTRIBUTES] = {"registry:one": ["z", "a", "a"]}
    result = agent_config.normalize_agent_config(defaults)
    assert result[CONF_EXPOSED_ENTITY_ATTRIBUTES] == {"registry:one": ["a", "z"]}
    defaults.pop(CONF_EXPOSED_ENTITY_ATTRIBUTES)
    assert CONF_EXPOSED_ENTITY_ATTRIBUTES not in agent_config.normalize_agent_config(
        defaults, apply_defaults=False
    )
    assert (
        agent_config.normalize_agent_config(defaults)[CONF_EXPOSED_ENTITY_ATTRIBUTES]
        == {}
    )
    with pytest.raises(agent_config.AgentConfigError, match="must be an object"):
        agent_config.normalize_agent_config("legacy")


def test_deleted_frontend_methods_have_no_source_or_bundle_references():
    for path in (ROOT / "frontend").rglob("*.js"):
        source = path.read_text(encoding="utf-8")
        assert "_overview(" not in source, path
        assert "_usageBar(" not in source, path
    assert not (ROOT / "lifecycle_optimizations.py").exists()
