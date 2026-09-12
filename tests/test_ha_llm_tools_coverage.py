"""Focused branch coverage for Home Assistant-owned LLM tool discovery."""

import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import ha_llm_tools
from homeassistant.components import llm as llm_component
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from tests.test_ha_llm_tools import Echo, TestAPI, context, reference, register


async def test_schema_falls_back_when_ha_converter_is_unavailable(hass, monkeypatch):
    """Older HA converter availability still yields a live OpenAPI schema."""
    monkeypatch.setattr(llm, "to_openapi", None, raising=False)
    register(hass, TestAPI(hass))

    snapshot = await ha_llm_tools.async_discover(hass, context())

    live = next(iter(snapshot.tools.values()))
    assert live.spec["parameters"]["properties"]["value"]["type"] == "string"


async def test_live_tool_refuses_dispatch_after_source_tool_replacement(hass):
    """A stale discovery result must not dispatch to a replacement implementation."""
    api = register(hass, TestAPI(hass))
    live = next(iter((await ha_llm_tools.async_discover(hass, context())).tools.values()))
    original = api.tools[0]
    replacement = Echo()
    live.instance.tools[:] = [replacement]

    with pytest.raises(HomeAssistantError, match="changed before dispatch"):
        await live.async_call(
            llm.ToolInput(tool_name="echo", tool_args={"value": "blocked"})
        )

    assert original.calls == []
    assert replacement.calls == []


async def test_namespaced_tools_are_preview_filtered_but_allowed_for_caller_api(hass):
    """Merged display names are not persisted, while caller-owned instances remain usable."""
    api = TestAPI(hass, tools=[llm.NamespacedTool("remote", Echo())])
    register(hass, api)

    preview = await ha_llm_tools.async_discover(hass, context())
    caller_snapshot, caller_tools = ha_llm_tools.caller_api_tools(
        await api.async_get_api_instance(context())
    )

    assert preview.tools == {}
    assert caller_snapshot.caller_provided is True
    assert len(caller_snapshot.tools) == 1
    assert len(caller_tools) == 1
    assert caller_tools[0]["ha_available"] is True


async def test_bad_tool_schema_is_isolated_from_other_tools(hass, monkeypatch, caplog):
    """One converter failure must not make a sibling HA tool unavailable."""
    broken = Echo()
    broken.name = "broken"
    usable = Echo()
    usable.name = "usable"
    register(hass, TestAPI(hass, tools=[broken, usable]))
    original_schema = ha_llm_tools._schema

    def schema(tool, serializer):
        if tool.name == "broken":
            raise ValueError("unsupported schema")
        return original_schema(tool, serializer)

    monkeypatch.setattr(ha_llm_tools, "_schema", schema)

    snapshot = await ha_llm_tools.async_discover(hass, context())

    assert [live.tool.name for live in snapshot.tools.values()] == ["usable"]
    assert "HA LLM tool schema unavailable" in caplog.text


async def test_empty_selection_and_merged_api_fail_closed(hass):
    """No selected APIs does no work, and merged APIs are never persisted as sources."""
    first = TestAPI(hass, "one")
    second = TestAPI(hass, "two")
    register(hass, llm.MergedAPI([first, second]))

    empty = await ha_llm_tools.async_discover(hass, context(), [])
    preview = await ha_llm_tools.async_discover(hass, context())

    assert empty.tools == {}
    assert first.contexts == []
    assert second.contexts == []
    assert preview.tools == {}


async def test_selected_api_skips_unreferenced_registered_api(hass):
    """Reference-scoped discovery must not instantiate unrelated custom APIs."""
    selected_api = register(hass, TestAPI(hass, "selected"))
    unrelated = register(hass, TestAPI(hass, "unrelated"))
    preview = await ha_llm_tools.async_discover(hass, context())
    selected_reference = next(
        live.reference
        for live in preview.tools.values()
        if live.reference["api_id"] == "selected"
    )
    selected_api.contexts.clear()
    unrelated.contexts.clear()

    snapshot = await ha_llm_tools.async_discover(
        hass, context(), [selected_reference]
    )

    assert len(snapshot.tools) == 1
    assert next(iter(snapshot.tools.values())).reference["api_id"] == "selected"
    assert len(selected_api.contexts) == 1
    assert unrelated.contexts == []


async def test_assist_registry_unavailable_is_reported_without_raising(hass):
    """A missing contributor registry marks Assist unavailable rather than remapping it."""
    register(hass, llm_component.AssistAPI(hass))

    snapshot = await ha_llm_tools.async_discover(hass, context())

    assert snapshot.tools == {}
    assert snapshot.unavailable_sources == ["assist"]


async def test_platform_selection_and_empty_contribution_are_isolated(hass):
    """Only selected contributor domains run, and a platform may contribute no tools."""
    register(hass, llm_component.AssistAPI(hass))
    selected = MagicMock(
        return_value=llm_component.LLMTools(tools=[Echo()], prompt="selected")
    )
    ignored = MagicMock(
        return_value=llm_component.LLMTools(tools=[Echo()], prompt="ignored")
    )
    empty = MagicMock(return_value=None)
    hass.data[llm_component.DATA_PLATFORMS] = SimpleNamespace(
        async_get_platforms=AsyncMock(
            return_value={
                "empty": SimpleNamespace(async_get_tools=empty),
                "ignored": SimpleNamespace(async_get_tools=ignored),
                "selected": SimpleNamespace(async_get_tools=selected),
            }
        )
    )

    scoped = await ha_llm_tools.async_discover(
        hass, context(), [reference(source="selected")]
    )

    assert len(scoped.tools) == 1
    assert next(iter(scoped.tools.values())).reference["source_id"] == "selected"
    selected.assert_called_once()
    ignored.assert_not_called()
    empty.assert_not_called()

    preview = await ha_llm_tools.async_discover(hass, context())

    assert {live.reference["source_id"] for live in preview.tools.values()} == {
        "ignored",
        "selected",
    }
    assert preview.unavailable_sources == []
    empty.assert_called_once()


async def test_discovery_without_component_module_still_supports_custom_api(
    hass, monkeypatch
):
    """Optional platform discovery must not be required for opaque custom APIs."""
    api = register(hass, TestAPI(hass))
    real_import = builtins.__import__

    def import_without_component_llm(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "homeassistant.components" and "llm" in fromlist:
            raise ImportError("component unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_component_llm)

    snapshot = await ha_llm_tools.async_discover(hass, context())

    assert len(snapshot.tools) == 1
    assert next(iter(snapshot.tools.values())).reference["api_id"] == api.id
