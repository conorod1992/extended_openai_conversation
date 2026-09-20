"""The retired standalone Memory surface cannot be packaged or registered."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from custom_components.extended_openai_conversation_responses import management_ui

COMPONENT = Path(management_ui.__file__).parent


def test_standalone_memory_files_and_production_references_are_absent():
    names = ("memory_ui.py", "memory-panel.js", "memory-management-panel.js")
    for name in names:
        assert not list(COMPONENT.rglob(name))
    for path in [*COMPONENT.rglob("*.py"), *COMPONENT.rglob("*.js")]:
        source = path.read_text(encoding="utf-8")
        assert "extended-openai-memory" not in source, path
        assert "async_setup_memory_ui" not in source, path
        assert 'f"{DOMAIN}/manage"' not in source, path
    manifest = (COMPONENT / "frontend/dist/manifest.json").read_text()
    config = Path("frontend/vite.config.ts").read_text()
    for name in names:
        assert name not in manifest
        assert name not in config


async def test_management_setup_registers_only_live_panel_and_command(monkeypatch):
    hass = MagicMock()
    hass.data = {}
    assets = AsyncMock()
    panel = AsyncMock()
    command = MagicMock()
    monkeypatch.setattr(management_ui, "async_register_frontend_assets", assets)
    monkeypatch.setattr(
        management_ui, "frontend_entry_url", lambda *_: "/frontend/management-test.js"
    )
    monkeypatch.setattr(management_ui.panel_custom, "async_register_panel", panel)
    monkeypatch.setattr(management_ui.websocket_api, "async_register_command", command)
    await management_ui.async_setup_management_ui(hass)
    await management_ui.async_setup_management_ui(hass)
    assets.assert_awaited_once_with(hass)
    command.assert_called_once_with(hass, management_ui.websocket_management)
    assert panel.await_count == 1
    assert panel.await_args.kwargs["frontend_url_path"] == "extended-openai"
    assert (
        panel.await_args.kwargs["webcomponent_name"]
        == "extended-openai-management-panel"
    )
