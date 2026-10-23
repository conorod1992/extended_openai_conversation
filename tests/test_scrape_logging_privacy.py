"""Real Scrape coordinator diagnostics exclude unselected private responses."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execute_backend,
)
from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.template_diagnostics import (
    private_rendering_diagnostics,
)
from homeassistant.exceptions import HomeAssistantError

LOGGER = "homeassistant.components.scrape.coordinator"
SECRET = "FABRICATED-hidden-backend-csrf-token-917"
BODY = (
    '<html><title>Ordinary title</title><input name="csrf_token" value="'
    + SECRET
    + '"></html>'
)


def scrape_config():
    function = web.ScrapeFunction()
    config = function.validate_schema(
        {
            "type": "scrape",
            "resource": "https://example.invalid/account?api_key=" + SECRET,
            "sensor": [{"name": "title", "select": "title"}],
        }
    )
    return function, config


async def execute_scrape(hass, function, config, *, owned):
    if owned:
        return await execute_backend(function, hass, config, {}, None, [])
    return await function.execute(hass, config, {}, None, [])


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("level", [logging.DEBUG, logging.WARNING])
async def test_real_scrape_response_is_private_before_every_handler(
    hass, monkeypatch, caplog, owned, level
):
    # Keep the real coordinator, parser and extraction; replace only HTTP input.
    data = SimpleNamespace(
        data=BODY,
        headers={"Content-Type": "text/html"},
        async_update=AsyncMock(),
    )
    monkeypatch.setattr(web, "get_rest_data", lambda *_: data)
    function, config = scrape_config()
    caplog.set_level(level, logger=LOGGER)
    caplog.set_level(logging.DEBUG, logger=web.__name__)
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append((record.name, record.getMessage(), record.exc_info))

    logger = logging.getLogger(LOGGER)
    handler = Capture()
    logger.addHandler(handler)
    try:
        result = await execute_scrape(hass, function, config, owned=owned)
    finally:
        logger.removeHandler(handler)
    assert result == "Ordinary title"
    assert SECRET not in caplog.text
    assert all(SECRET not in name + message for name, message, _ in captured)
    assert all(exc_info is None for _, _, exc_info in captured)
    assert "extraction=0 matches=1 value_available=True" in caplog.text
    if level == logging.DEBUG:
        assert captured
        assert "EOAI private scrape diagnostic" in caplog.text
        assert "EOAI private scrape refresh; duration=" in caplog.text
        assert "success=True" in caplog.text
        assert any(r.funcName == "_async_update_data" for r in caplog.records)
    else:
        assert not captured
        assert "EOAI private scrape refresh" not in caplog.text


@pytest.mark.parametrize("owned", [False, True])
async def test_real_failed_scrape_keeps_warning_level_failure_category(
    hass, monkeypatch, caplog, owned
):
    data = SimpleNamespace(data=None, headers={}, async_update=AsyncMock())
    monkeypatch.setattr(web, "get_rest_data", lambda *_: data)
    function, config = scrape_config()
    caplog.set_level(logging.WARNING, logger=LOGGER)
    with pytest.raises(HomeAssistantError):
        await execute_scrape(hass, function, config, owned=owned)
    records = [record for record in caplog.records if record.name == LOGGER]
    assert records and records[0].levelno == logging.ERROR
    assert "error_type=UpdateFailed" in records[0].getMessage()
    assert SECRET not in caplog.text
    assert all(record.exc_info is None for record in records)


async def test_concurrent_unrelated_scrape_logs_keep_their_normal_content(
    hass, monkeypatch, caplog
):
    ready, release = asyncio.Event(), asyncio.Event()

    async def fetch():
        ready.set()
        await release.wait()

    data = SimpleNamespace(
        data=BODY, headers={"Content-Type": "text/html"}, async_update=fetch
    )
    monkeypatch.setattr(web, "get_rest_data", lambda *_: data)
    function, config = scrape_config()
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    task = asyncio.create_task(execute_scrape(hass, function, config, owned=True))
    await ready.wait()
    unrelated = "unrelated-task-response"
    logger = logging.getLogger(LOGGER)
    try:
        logger.debug("Unrelated response %s", unrelated)
    finally:
        release.set()
        result = await task
    assert result == "Ordinary title"
    assert SECRET not in caplog.text
    assert "Unrelated response " + unrelated in caplog.text
    assert "EOAI private scrape refresh" in caplog.text
    # Returning from the direct/decorated backend does not alter other callers.
    logger.warning("After execution %s", unrelated)
    assert "After execution " + unrelated in caplog.text


def test_scrape_descendants_omit_exception_prose_and_traceback_attachments(caplog):
    logger = logging.getLogger(LOGGER + "." + SECRET)
    with private_rendering_diagnostics():
        try:
            raise ValueError(SECRET)
        except ValueError:
            logger.exception("Private response %s", BODY, stack_info=True)
    record = caplog.records[-1]
    assert record.name == LOGGER
    assert record.levelno == logging.ERROR
    assert "error_type=ValueError" in record.getMessage()
    assert record.funcName == (
        "test_scrape_descendants_omit_exception_prose_and_traceback_attachments"
    )
    assert SECRET not in caplog.text
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None


@pytest.mark.parametrize(
    "args",
    [
        (SECRET, SECRET, True),
        (SECRET, 0.2, SECRET),
        (SECRET, float("nan"), True),
        (SECRET, float("inf"), True),
        (SECRET, float("-inf"), True),
        (SECRET, 2**10_000, True),
        (SECRET, -1, True),
        (SECRET, 31_536_001, True),
    ],
)
def test_changed_coordinator_timing_shapes_are_not_assumed_public(caplog, args):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with private_rendering_diagnostics():
        logging.getLogger(LOGGER).debug(
            "Finished fetching %s data in %.3f seconds (success: %s)", *args
        )
    assert SECRET not in caplog.text
    assert "EOAI private scrape diagnostic" in caplog.text


@pytest.mark.parametrize("duration", [0, 1, 0.25, 31_536_000])
def test_bounded_primitive_coordinator_durations_remain_useful(caplog, duration):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with private_rendering_diagnostics():
        logging.getLogger(LOGGER).debug(
            "Finished fetching %s data in %.3f seconds (success: %s)",
            SECRET,
            duration,
            True,
        )
    assert SECRET not in caplog.text
    assert f"duration={duration:.3f} seconds success=True" in caplog.text


def test_future_structured_coordinator_messages_remain_private(caplog):
    with private_rendering_diagnostics():
        logging.getLogger(LOGGER).warning({"response": BODY})
    assert SECRET not in caplog.text
    assert "EOAI private scrape diagnostic" in caplog.text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Timeout fetching %s data", "timeout"),
        ("Fetching %s data recovered", "recovered"),
    ],
)
def test_safe_coordinator_health_categories_survive(caplog, message, expected):
    with private_rendering_diagnostics():
        logging.getLogger(LOGGER).warning(message, SECRET)
    assert "error_type=" + expected in caplog.text
    assert SECRET not in caplog.text
