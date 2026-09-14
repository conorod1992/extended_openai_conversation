"""Final malformed-scrape coverage for the Web function."""

from __future__ import annotations

from bs4 import BeautifulSoup

from homeassistant.const import CONF_ATTRIBUTE
from homeassistant.components import scrape

from custom_components.extended_openai_conversation_responses.functions.web import (
    ScrapeFunction,
)


def test_scrape_missing_selector_index_returns_none() -> None:
    """An out-of-range selector index is a clean missing value, not an exception."""
    function = object.__new__(ScrapeFunction)
    data = BeautifulSoup("<div class='item'>only</div>", "html.parser")

    result = function._extract_value(
        data,
        {
            scrape.const.CONF_SELECT: ".item",
            scrape.const.CONF_INDEX: 1,
        },
    )

    assert result is None


def test_scrape_missing_requested_attribute_returns_none() -> None:
    """A selected element without the requested attribute returns None cleanly."""
    function = object.__new__(ScrapeFunction)
    data = BeautifulSoup("<a class='item'>link</a>", "html.parser")

    result = function._extract_value(
        data,
        {
            scrape.const.CONF_SELECT: ".item",
            CONF_ATTRIBUTE: "href",
        },
    )

    assert result is None
