"""A real Home Assistant script consumes an EOAI AI Task service response."""
import pytest

from homeassistant.helpers.script import Script
from tests_real_ha.test_ai_task_runtime import _setup_entry


@pytest.mark.parametrize("answer,expected", [("ready", True), ("not-ready", False)])
async def test_native_script_consumes_ai_task_response_variable(
    hass, answer, expected
):
    _entry, entity_id, client = await _setup_entry(hass, [answer])
    triggered = []

    async def record(call):
        triggered.append(call.data["marker"])

    hass.services.async_register("journey", "record", record)
    sequence = [
        {
            "action": "ai_task.generate_data",
            "data": {
                "entity_id": entity_id,
                "task_name": "Native response variable probe",
                "instructions": "Return a readiness result",
            },
            "response_variable": "task_result",
        },
        {
            "choose": [
                {
                    "conditions": "{{ task_result.data == 'ready' }}",
                    "sequence": [
                        {"action": "journey.record", "data": {"marker": "ready"}}
                    ],
                }
            ],
            "default": [{"action": "journey.record", "data": {"marker": "not-ready"}}],
        },
    ]
    script = Script(hass, sequence, "EOAI AI Task consumer")
    await script.async_run()
    assert triggered == ["ready" if expected else "not-ready"]
    assert len(client.completions.calls) == 1
