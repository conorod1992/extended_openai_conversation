"""Real HA renderers retain useful diagnostics without private source in logs."""

import asyncio
import logging

import pytest

from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execute_backend,
)
from custom_components.extended_openai_conversation_responses.functions.script import (
    ScriptFunction,
)
from custom_components.extended_openai_conversation_responses.functions.template import (
    TemplateFunction,
)
from custom_components.extended_openai_conversation_responses.public_errors import (
    SanitizedTemplateError,
)
from custom_components.extended_openai_conversation_responses.template_diagnostics import (
    private_rendering_diagnostics,
    private_template_diagnostics,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template

SECRET = "FABRICATED-private-unused-key-42"
SOURCE = '{% set api_key = "' + SECRET + '" %}{{ missing }}'


async def test_successful_template_warning_contains_no_source(hass, caplog):
    result = await TemplateFunction().execute(
        hass, {"value_template": Template(SOURCE, hass)}, {}, None, []
    )
    assert SECRET not in str(result)
    assert SECRET not in caplog.text
    assert "EOAI private template diagnostic" in caplog.text
    assert any(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.parametrize("owned", [False, True])
async def test_real_script_error_logging_is_contained(tmp_path, caplog, owned):
    caplog.set_level(logging.DEBUG, logger="homeassistant.helpers.template")
    caplog.set_level(
        logging.DEBUG,
        logger="custom_components.extended_openai_conversation_responses.functions.script",
    )
    hass = HomeAssistant(str(tmp_path))
    function = ScriptFunction()
    config = function.validate_schema(
        {
            "type": "script",
            "sequence": [
                {
                    "variables": {
                        "value": '{"password":"'
                        + SECRET
                        + '", "value": {{ bad | int }}}'
                    }
                }
            ],
        }
    )
    try:
        with pytest.raises((TemplateError, SanitizedTemplateError)) as caught:
            if owned:
                await execute_backend(
                    function, hass, config, {"bad": "invalid"}, None, []
                )
            else:
                await function.execute(hass, config, {"bad": "invalid"}, None, [])
        assert isinstance(
            caught.value, TemplateError
        ) or "Template rendering failed" in str(caught.value)
        assert SECRET not in caplog.text
        assert "EOAI private script diagnostic; step=1" in caplog.text
        assert "error_type=template" in caplog.text
        assert any(record.levelno == logging.ERROR for record in caplog.records)
        assert all(record.exc_info is None for record in caplog.records)
    finally:
        await hass.async_stop(force=True)


async def test_request_rule_condition_warning_is_private(hass, caplog):
    from .test_request_rule_condition_lifecycle import saved_rule

    rules, _rule = await saved_rule(
        [{"condition": "template", "value_template": SOURCE}]
    )
    try:
        assert await rules.async_match(hass, "good night") is None
        assert SECRET not in caplog.text
        assert "EOAI private template diagnostic" in caplog.text
    finally:
        rules.async_unload()


async def test_log_filter_is_task_local_and_resets_after_cancellation(hass, caplog):
    ready = asyncio.Event()
    block = asyncio.Event()

    @private_template_diagnostics
    async def private_task():
        await asyncio.create_task(asyncio.sleep(0))
        Template(SOURCE, hass).async_render({})
        ready.set()
        await block.wait()

    task = asyncio.create_task(private_task())
    await ready.wait()
    assert SECRET not in caplog.text
    caplog.clear()
    # Simultaneous unrelated HA rendering retains its usual source-bearing log.
    Template(SOURCE, hass).async_render({})
    assert SECRET in caplog.text
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    caplog.clear()
    with (
        pytest.raises(RuntimeError),
        private_rendering_diagnostics(),
        private_rendering_diagnostics(),
    ):
        raise RuntimeError("reset scope")
    Template(SOURCE, hass).async_render({})
    assert SECRET in caplog.text


def test_private_records_remove_tracebacks_but_preserve_severity_and_callsite(caplog):
    logger = logging.getLogger("homeassistant.helpers.script")
    with private_rendering_diagnostics():
        try:
            raise ValueError(SECRET)
        except ValueError:
            logger.exception("private %s", SECRET, stack_info=True)
    record = caplog.records[-1]
    assert SECRET not in caplog.text
    assert "ValueError" in record.getMessage()
    assert record.levelno == logging.ERROR
    assert (
        record.funcName
        == "test_private_records_remove_tracebacks_but_preserve_severity_and_callsite"
    )
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None
