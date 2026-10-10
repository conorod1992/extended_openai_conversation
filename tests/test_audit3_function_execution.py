"""Regression contracts for exact text, literal paths, scheduling and HA reads."""

from datetime import datetime
import sqlite3
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import ha_actions
from custom_components.extended_openai_conversation_responses.function_execution import (
    split_legacy_execution_delay,
    validate_function_arguments,
    validate_function_schema,
)
from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)
from custom_components.extended_openai_conversation_responses.functions.file import (
    EditFileFunction,
    WriteFileFunction,
)
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.functions.template import (
    TemplateFunction,
)
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers.template import Template
from tests.test_audit2_sqlite import examples, history as history, run


@pytest.mark.parametrize(
    "text", ["    indented\n", "\nbar\n", "    ", "", "\u2063\n", "{literal}"]
)
async def test_write_preserves_exact_rendered_text(hass, tmp_path, text):
    target = tmp_path / "extended_openai_conversation_responses" / "exact.txt"
    result = await WriteFileFunction().execute(
        hass,
        {
            "path": Template(str(target), hass),
            "content": Template("{{ content }}", hass),
        },
        {"content": text},
        None,
        [],
    )
    assert result["success"]
    assert target.read_bytes() == text.encode()


@pytest.mark.parametrize(
    "before,old,new,after",
    [
        ("    indented\n", "    ", "        ", "        indented\n"),
        ("bar\nx\nbar\nend", "\nbar\n", "\nBAZ\n", "bar\nx\nBAZ\nend"),
        ("a b", " ", "", "ab"),
    ],
)
async def test_edit_preserves_whitespace_boundaries(
    hass, tmp_path, before, old, new, after
):
    target = tmp_path / "extended_openai_conversation_responses" / "exact.txt"
    target.write_bytes(before.encode())
    config = {
        key: Template(value, hass)
        for key, value in {
            "path": str(target),
            "old_text": "{{ old }}",
            "new_text": "{{ new }}",
        }.items()
    }
    result = await EditFileFunction().execute(
        hass, config, {"old": old, "new": new}, None, []
    )
    assert result["success"]
    assert target.read_bytes() == after.encode()


@pytest.mark.parametrize(
    "command",
    [
        "cat ./notes.txt",
        "./script.sh",
        "curl https://example.com/status",
        "echo https://example.com/status",
        "curl 'https://example.com/?redirect=/status&key=value'",
        "curl --url=https://example.com/status",
    ],
)
def test_relative_paths_and_url_arguments_are_allowed(tmp_path, command):
    BashFunction()._guard_command(command, tmp_path, True)


@pytest.mark.parametrize(
    "command",
    [
        "cat /etc/passwd",
        "cat '/etc/passwd'",
        "cat ../private",
        "cd ..",
        "echo https://example.com;/etc/passwd",
        "echo https://example.com >/etc/passwd",
    ],
)
def test_urls_do_not_hide_outside_workspace_paths(tmp_path, command):
    with pytest.raises(ValueError):
        BashFunction()._guard_command(command, tmp_path, True)


@pytest.mark.parametrize(
    "parent,unit",
    [
        ("object", ["integer", "null"]),
        (["object", "null"], "integer"),
        (["object", "null"], ["number", "null"]),
    ],
)
def test_nullable_delay_contract_still_schedules(parent, unit):
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "delay": {"type": parent, "properties": {"seconds": {"type": unit}}}
            },
        }
    }
    validate_function_schema(spec["parameters"])
    arguments = validate_function_arguments(spec, {"delay": {"seconds": 600}})
    assert split_legacy_execution_delay(spec, arguments) == ({}, {"seconds": 600})
    assert split_legacy_execution_delay(spec, {"delay": None}) == ({}, None)
    assert split_legacy_execution_delay(spec, {"delay": {"seconds": None}}) == (
        {},
        None,
    )


@pytest.mark.parametrize(
    "support", [SupportsResponse.NONE, SupportsResponse.OPTIONAL, SupportsResponse.ONLY]
)
async def test_native_services_propagate_supported_responses(tmp_path, support):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("weather.demo", "sunny")

    async def handler(call):
        return (
            {"forecast": [{"temperature": 21}]}
            if support is not SupportsResponse.NONE
            else None
        )

    hass.services.async_register("weather", "audit", handler, supports_response=support)
    result = await NativeFunction().execute_service_single(
        hass,
        {},
        {
            "domain": "weather",
            "service": "audit",
            "service_data": {"entity_id": "weather.demo"},
        },
        None,
        [{"entity_id": "weather.demo"}],
    )
    assert result["success"]
    if support is SupportsResponse.NONE:
        assert "response" not in result
    else:
        assert result["response"] == {"forecast": [{"temperature": 21}]}


async def test_required_response_is_requested_even_for_legacy_callers(hass):
    hass.services.supports_response.return_value = SupportsResponse.ONLY
    hass.services.async_call = AsyncMock(return_value={"answer": 42})
    assert (
        await ha_actions._async_call_ha_action_unchecked(hass, "weather", "audit") == {}
    )
    assert hass.services.async_call.await_args.kwargs["return_response"] is True
    assert hass.services.async_call.await_args.kwargs["blocking"] is True


async def test_read_only_template_accepts_unavailable_exposed_entities(hass):
    hass.states.get.return_value.state = "unavailable"
    function = TemplateFunction()
    config = function.validate_schema(
        {"type": "template", "value_template": "{{ historical }}"}
    )
    assert (
        await function.execute(
            hass,
            config,
            {"entity_id": "sensor.temperature", "historical": 21},
            None,
            [{"entity_id": "sensor.temperature"}],
        )
        == "21"
    )
    with pytest.raises(Exception, match="access is denied"):
        await function.execute(
            hass,
            config,
            {"entity_id": "sensor.temperature", "historical": 21},
            None,
            [],
        )


@pytest.mark.parametrize(
    "instant,expected", [("2026-01-01 01:00:00", "on"), ("2026-01-01 03:00:00", "off")]
)
async def test_published_point_in_time_query_uses_retained_state_at_or_before(
    hass, history, instant, expected
):
    step = examples()["get_state_at_time"]["function"]
    with sqlite3.connect(history) as conn:
        conn.execute(
            "INSERT INTO states VALUES (?, ?, ?, ?, ?)",
            (5, 1, 2, "future", datetime(2026, 1, 1, 3).timestamp() + 0.5),
        )
    result = await run(
        hass, history, step, {"entity_id": "light.public", "datetime": instant}
    )
    assert result[0]["state"] == expected


async def test_literal_config_whitespace_is_preserved(hass, tmp_path):
    target = tmp_path / "extended_openai_conversation_responses" / "literal.txt"
    function = WriteFileFunction()
    config = function.validate_schema(
        {"type": "write_file", "path": str(target), "content": "    literal\n\n"}
    )
    result = await function.execute(hass, config, {}, None, [])
    assert result["success"]
    assert target.read_bytes() == b"    literal\n\n"
