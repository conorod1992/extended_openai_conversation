"""Backend failure metadata is explicit, never inferred from returned fields."""

from collections.abc import Mapping
from contextlib import nullcontext
from functools import partial
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.function_execution import (
    propagate_function_execution_errors,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
    bind_tool_recovery_state,
)
from custom_components.extended_openai_conversation_responses.functions.file import (
    EditFileFunction,
    ReadFileFunction,
    WriteFileFunction,
)
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
    _service_participants,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import service as service_helpers
from homeassistant.helpers.template import Template


@pytest.mark.parametrize("context", ["legacy", "rule", "strict"])
@pytest.mark.parametrize("backend", ["native", "read", "write", "edit"])
async def test_backend_owned_failure_honours_context(
    hass, exposed_entities, context, backend, tmp_path
):
    scope = {
        "legacy": nullcontext(),
        "rule": propagate_function_execution_errors(),
        "strict": bind_tool_recovery_state(ToolRecoveryState(enabled=True)),
    }[context]
    if backend == "native":
        hass.services.async_call.side_effect = HomeAssistantError("Device unavailable")
        function = NativeFunction()
        config = {"name": "execute_service"}
        arguments = {
            "list": [
                {
                    "domain": "light",
                    "service": "turn_on",
                    "service_data": {"entity_id": "light.living_room"},
                }
            ]
        }
    else:
        function = {
            "read": ReadFileFunction,
            "write": WriteFileFunction,
            "edit": EditFileFunction,
        }[backend]()
        config = {"path": Template(str(tmp_path / "missing" / "file.txt"), hass)}
        if backend == "write":
            # A directory cannot be replaced by a text file.
            config["path"] = Template(str(tmp_path), hass)
            config["content"] = Template("content", hass)
        if backend == "edit":
            config.update(
                old_text=Template("old", hass), new_text=Template("new", hass)
            )
        arguments = {}
    with scope:
        if context == "legacy":
            result = await function.execute(
                hass, config, arguments, None, exposed_entities
            )
            assert "error" in (result[0] if backend == "native" else result)
        else:
            with pytest.raises(HomeAssistantError):
                await function.execute(hass, config, arguments, None, exposed_entities)


def test_participant_lookup_does_not_copy_or_scan_registered_entities(hass):
    class Candidates(Mapping):
        def __getitem__(self, key):
            if key == "light.audit":
                return object()
            raise KeyError(key)

        def __iter__(self):
            raise AssertionError("Full entity scan is unnecessary")

        def __len__(self):
            return 100_000

    target = partial(
        service_helpers.entity_service_call, hass, Candidates(), "async_turn_on"
    )
    hass.services.async_services_for_domain.return_value = {
        "turn_on": SimpleNamespace(job=SimpleNamespace(target=target))
    }
    assert _service_participants(
        hass, "light", "turn_on", {"light.audit", "sensor.unrelated"}
    ) == {"light.audit"}
