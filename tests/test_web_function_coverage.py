"""Focused coverage for web function result handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from bs4 import BeautifulSoup
import pytest

from homeassistant.const import CONF_ATTRIBUTE, CONF_VALUE_TEMPLATE

from custom_components.extended_openai_conversation_responses.functions import web


@pytest.mark.asyncio
async def test_rest_execute_preserves_none_without_rendering_value_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not render a REST value template when the response has no value."""

    class FakeRestData:
        async def async_update(self) -> None:
            pass

        def data_without_xml(self) -> None:
            return None

    monkeypatch.setattr(web, "get_rest_data", lambda hass, config, arguments: FakeRestData())
    value_template = MagicMock()

    result = await web.RestFunction.execute(
        web.RestFunction.__new__(web.RestFunction),
        SimpleNamespace(),
        {CONF_VALUE_TEMPLATE: value_template},
        {"query": "test"},
        None,
        [],
    )

    assert result is None
    value_template.async_render_with_possible_json_value.assert_not_called()


def test_render_value_exposes_parsed_value_json() -> None:
    """Expose parsed JSON alongside the original value to scrape templates."""
    value_template = MagicMock()
    value_template.async_render.return_value = "21"

    result = web.ScrapeFunction._render_value(
        value_template,
        '{"temperature":21}',
        {"location": "Carlow"},
    )

    assert result == "21"
    value_template.async_render.assert_called_once_with(
        {
            "location": "Carlow",
            "value": '{"temperature":21}',
            "value_json": {"temperature": 21},
        },
        parse_result=False,
    )


def test_extract_value_handles_attribute_raw_text_and_normal_text() -> None:
    """Extract configured attributes, raw script content, and ordinary tag text."""
    data = BeautifulSoup(
        '<a id="link" href="/status">Status</a>'
        '<script id="payload">{"ok": true}</script>'
        '<div id="message">Hello <b>world</b></div>',
        "html.parser",
    )
    function = web.ScrapeFunction.__new__(web.ScrapeFunction)

    assert function._extract_value(
        data,
        {web.scrape.const.CONF_SELECT: "#link", CONF_ATTRIBUTE: "href"},
    ) == "/status"
    assert function._extract_value(
        data,
        {web.scrape.const.CONF_SELECT: "#payload"},
    ) == '{"ok": true}'
    assert function._extract_value(
        data,
        {web.scrape.const.CONF_SELECT: "#message"},
    ) == "Hello world"

    assert (
        function._extract_value(
            data,
            {
                web.scrape.const.CONF_SELECT: ".item",
                web.scrape.const.CONF_INDEX: 1,
            },
        )
        is None
    )
    assert (
        function._extract_value(
            data,
            {
                web.scrape.const.CONF_SELECT: "#message",
                CONF_ATTRIBUTE: "href",
            },
        )
        is None
    )
