"""Legacy inspection keeps transport work off-loop and HA schemas on-loop."""

import asyncio
from copy import deepcopy
import json
import threading
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_function_repair as repair,
)
from custom_components.extended_openai_conversation_responses.functions.web import (
    RestFunction,
    ScrapeFunction,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    InvalidFunction,
)
from tests.test_backup import _document
from custom_components.extended_openai_conversation_responses.schema_errors import (
    SCHEMA_ERRORS,
)


@pytest.mark.parametrize("kind", ["template", "native", "composite", "disabled"])
async def test_async_inspection_keeps_all_ha_validation_on_loop(
    hass, monkeypatch, kind
):
    document = _document()
    function = {"type": "template", "value_template": "{{ 1 + 1 }}"}
    if kind == "native":
        function = {"type": "native", "name": "execute_service_single"}
    if kind == "composite":
        function = {
            "type": "composite",
            "sequence": [
                function,
                {"type": "native", "name": "execute_service_single"},
            ],
        }
    tool = {
        "enabled": kind != "disabled",
        "spec": {"name": f"context_{kind}", "parameters": {"type": "object"}},
        "function": function,
    }
    document["agent"]["config"].update(functions=[tool], function_groups=[])
    main_thread = threading.get_ident()
    loop = asyncio.get_running_loop()
    decoded = []
    decode = backup._decode_backup_document
    validate = backup._inspect_decoded_backup

    def decode_in_worker(*args):
        assert threading.get_ident() != main_thread
        decoded.append(True)
        return decode(*args)

    def validate_in_loop(*args, **kwargs):
        assert asyncio.get_running_loop() is loop
        return validate(*args, **kwargs)

    monkeypatch.setattr(backup, "_decode_backup_document", decode_in_worker)
    monkeypatch.setattr(backup, "_inspect_decoded_backup", validate_in_loop)
    prepared = await backup.async_inspect_backup(json.dumps(document), "agent")
    assert decoded == [True]
    assert prepared.title == "Jarvis"
    assert prepared.config["functions"]
    # The exact same validated categories are used by synchronous restore.
    monkeypatch.setattr(backup, "_decode_backup_document", decode)
    assert backup.inspect_backup(document, "agent").summary() == prepared.summary()


async def test_inspection_does_not_quarantine_valid_siblings(hass):
    document = _document()
    tools = [
        {
            "spec": {
                "name": "valid_inspection_sibling",
                "parameters": {"type": "object"},
            },
            "function": {"type": "template", "value_template": "{{ 42 }}"},
        },
        {
            "spec": {
                "name": "invalid_inspection_sibling",
                "parameters": {"type": "object"},
            },
            "function": {"type": "native", "name": "missing_implementation"},
        },
    ]
    document["agent"]["config"].update(functions=tools, function_groups=[])
    options = {"functions": deepcopy(tools)}
    before = repair.isolated_function_tools(options)
    await backup.async_inspect_backup(document, "agent")
    after = repair.isolated_function_tools(options)
    assert [tool["spec"]["name"] for tool in before[0]] == ["valid_inspection_sibling"]
    assert after == before


@pytest.mark.parametrize("value", ["{broken", "[]", {"format": "incorrect"}])
async def test_async_inspection_preserves_malformed_document_rejection(value):
    with pytest.raises(backup.BackupError):
        await backup.async_inspect_backup(value, "agent")


async def test_async_inspection_enforces_size_before_ha_validation(monkeypatch):
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", 16)
    with pytest.raises(backup.BackupError, match="safety limit"):
        await backup.async_inspect_backup(" " * 17, "agent")


@pytest.mark.parametrize("sensor", [None, [], {}, "bad"])
def test_scrape_rejects_missing_empty_and_malformed_extractions(hass, sensor):
    config = {"type": "scrape", "resource": "https://example.com"}
    if sensor is not None:
        config["sensor"] = sensor
    with pytest.raises(InvalidFunction):
        ScrapeFunction().validate_schema(config)
    assert RestFunction().validate_schema(
        {"type": "rest", "resource": "https://example.com"}
    )


def test_scrape_preserves_supported_sensor_normalization(hass):
    config = ScrapeFunction().validate_schema(
        {
            "type": "scrape",
            "resource": "https://example.com",
            "sensor": {"select": ".value"},
        }
    )
    assert len(config["sensor"]) == 1
    assert config["sensor"][0]["select"] == ".value"


@pytest.mark.parametrize("error_type", SCHEMA_ERRORS)
def test_public_function_validator_wraps_supported_schema_errors(error_type):
    function = RestFunction()
    function.data_schema = Mock(side_effect=error_type("malformed configuration"))
    with pytest.raises(InvalidFunction):
        function.validate_schema({"type": "rest"})
