"""Native creation and HA argument transformation use their public contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import config_flow
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    LiveTool,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.helpers import intent, llm


@pytest.mark.parametrize("length", [255, 256])
async def test_native_assistant_title_limit(hass, tmp_path, length):
    flow = SimpleNamespace(
        options=agent_config_defaults(),
        _is_new=True,
        _get_entry=lambda: SimpleNamespace(state=ConfigEntryState.LOADED),
        _async_get_skills=AsyncMock(return_value=[]),
        async_create_entry=Mock(
            side_effect=lambda **kwargs: {"type": "create_entry", **kwargs}
        ),
        async_show_form=Mock(side_effect=lambda **kwargs: {"type": "form", **kwargs}),
    )
    result = await config_flow.ExtendedOpenAISubentryFlowHandler.async_step_init(
        flow, {"name": "A" * length}
    )
    if length == 256:
        assert result["type"] == "form"
        assert result["errors"] == {"name": "invalid_name"}
        flow.async_create_entry.assert_not_called()
    else:
        assert result["title"] == "A" * 255
        from tests.test_backup import _document
        from custom_components.extended_openai_conversation_responses import (
            backup,
            backup_transfer,
            transfer,
        )

        document = _document()
        document["agent"]["title"] = result["title"]
        document["agent"]["config"] = result["data"]
        assert backup.inspect_backup(document, "agent").title == result["title"]
        assert transfer._inspect_full_backup(document, "agent").title == result["title"]
        archive = backup_transfer._build_archive_file(document, str(tmp_path))
        decoded = backup_transfer._load_archive_document(archive["path"])
        assert backup.inspect_backup(decoded, "agent").title == result["title"]
        from tests.test_transfer import _portable_document

        setup = _portable_document(mode="setup")
        setup["agent"]["title"] = result["title"]
        setup["sections"]["configuration"] = result["data"]
        exported = transfer.finalize_setup_export(setup)
        assert (
            transfer.inspect_transfer(exported["document"], "agent").title
            == result["title"]
        )


async def test_real_light_intent_receives_original_named_colour(hass, monkeypatch):
    from homeassistant.components.light.intent import async_setup_intents
    from homeassistant.util.color import RGBColor

    await async_setup_intents(hass)
    handler = next(
        item for item in intent.async_get(hass) if item.intent_type == "HassLightSet"
    )
    tool = llm.IntentTool("HassLightSet", handler)
    context = SimpleNamespace(
        platform="test",
        context=Context(),
        language="en",
        device_id=None,
        assistant=None,
    )
    # Run HA's actual intent dispatch/slot validation, replacing only its effect.
    seen = []

    async def effect(call):
        slots = handler.async_validate_slots(call.slots)
        seen.append((slots, call.context))
        response = call.create_response()
        response.async_set_speech("Done")
        return response

    monkeypatch.setattr(handler, "async_handle", effect)
    instance = llm.APIInstance(
        api=SimpleNamespace(hass=hass), api_prompt="", llm_context=context, tools=[tool]
    )
    live = LiveTool(
        reference={},
        tool=tool,
        instance=instance,
        source_label="Assist",
        prompt="",
        spec={},
    )
    arguments = {"name": "Kitchen", "color": "red"}
    await live.async_call(
        llm.ToolInput(tool_name="HassLightSet", tool_args=arguments, id="call-colour")
    )
    assert seen[0][0]["color"]["value"] == RGBColor(255, 0, 0)
    assert seen[0][1] is context.context
    assert arguments == {"name": "Kitchen", "color": "red"}
