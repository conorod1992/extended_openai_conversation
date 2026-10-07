"""Guard the Home Assistant contracts EOAI relies on across supported versions."""

from __future__ import annotations

from inspect import Parameter, signature

from homeassistant.components import conversation, panel_custom, websocket_api
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import ServiceRegistry
from homeassistant.helpers import area_registry, device_registry, entity_registry
from homeassistant.helpers.storage import Store


def _require_parameters(callable_obj, *names: str) -> None:
    """Fail with a focused message when an HA callable changes its usable contract."""
    params = signature(callable_obj).parameters
    missing = [name for name in names if name not in params]
    assert not missing, (
        f"Home Assistant contract drift in {callable_obj!r}: "
        f"missing parameters {missing}; available={list(params)}"
    )
    for name in names:
        assert params[name].kind not in {
            Parameter.VAR_POSITIONAL,
            Parameter.VAR_KEYWORD,
        }


def test_home_assistant_semantic_contracts_used_by_eoai() -> None:
    """Pin capabilities and argument names that EOAI calls directly.

    This deliberately avoids a whole-object/version snapshot. It records only
    public seams whose semantics EOAI depends on, so HA-dev drift fails close to
    the changed boundary instead of waiting for an unrelated journey to notice.
    """
    _require_parameters(
        conversation.async_converse,
        "hass",
        "text",
        "conversation_id",
        "context",
        "language",
        "agent_id",
        "device_id",
    )

    for method, required in (
        (ConfigEntries.async_setup, ("entry_id",)),
        (ConfigEntries.async_unload, ("entry_id",)),
        (ConfigEntries.async_reload, ("entry_id",)),
        (ConfigEntries.async_update_entry, ("entry",)),
        (ConfigEntries.async_update_subentry, ("entry", "subentry")),
    ):
        _require_parameters(method, *required)

    _require_parameters(
        ServiceRegistry.async_call,
        "domain",
        "service",
        "service_data",
        "blocking",
        "context",
    )

    for registry in (entity_registry, device_registry, area_registry):
        assert callable(registry.async_get), (
            f"Home Assistant registry contract drift: {registry.__name__}.async_get"
        )

    for method in (Store.async_load, Store.async_save, Store.async_remove):
        assert callable(method), f"Home Assistant Store contract drift: {method!r}"

    # EOAI's management panel and authenticated management API use these native
    # frontend/WebSocket registration seams rather than private HTTP endpoints.
    assert callable(panel_custom.async_register_panel)
    assert callable(websocket_api.async_register_command)
    assert callable(websocket_api.websocket_command)


def test_store_and_websocket_registration_contract_shapes() -> None:
    """Protect the small signature details most likely to break silently."""
    _require_parameters(Store.__init__, "hass", "version", "key")
    _require_parameters(Store.async_save, "data")
    _require_parameters(websocket_api.async_register_command, "hass", "command_or_handler")
