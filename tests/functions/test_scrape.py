"""Tests for ScrapeFunction using yaml definitions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bs4 import BeautifulSoup
import pytest

from homeassistant.components import scrape
from homeassistant.const import CONF_VALUE_TEMPLATE

# Import Tools and test helpers
from custom_components.extended_openai_conversation_responses.functions import (
    ScrapeFunction,
)
from tests.helpers import prepare_function_tool_from_yaml


class TestScrapeFunctionYaml:
    """Test ScrapeFunction using yaml definitions."""

    @pytest.fixture
    def function(self):
        """Create ScrapeFunction instance."""
        return ScrapeFunction()

    async def test_execute_scrape_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test web scraping from yaml definition."""
        # Load function from yaml
        function_tool = prepare_function_tool_from_yaml("scrape_example.yaml")
        function_config = function_tool["function"]

        with (
            patch(
                "custom_components.extended_openai_conversation_responses.functions.web.rest.create_rest_data_from_config"
            ) as mock_rest,
            patch(
                "custom_components.extended_openai_conversation_responses.functions.web.scrape.coordinator.ScrapeCoordinator"
            ) as mock_coordinator_class,
        ):
            mock_rest_data = AsyncMock()
            mock_rest.return_value = mock_rest_data

            mock_coordinator = AsyncMock()
            # Mock Hacker News HTML structure
            html = """
            <html>
                <tr class="athing">
                    <td class="title">
                        <span class="titleline">
                            <a href="https://example.com/article">Test Article Title</a>
                        </span>
                    </td>
                </tr>
                <tr>
                    <td colspan="2">
                        <span class="score">123 points</span>
                    </td>
                </tr>
            </html>
            """
            mock_coordinator.data = BeautifulSoup(html, "html.parser")
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator_class.return_value = mock_coordinator

            # Arguments based on yaml spec parameters (category is optional)
            arguments = {}

            result = await function.execute(
                hass, function_config, arguments, llm_context, exposed_entities
            )

            # Should return scraped data
            assert result is not None

    async def test_html_extraction_uses_executor_before_template_render(self, function):
        """Keep BeautifulSoup work off-loop while rendering HA templates on-loop."""
        data = BeautifulSoup('<span class="value">raw value</span>', "html.parser")
        value_template = MagicMock()
        value_template.async_render_with_possible_json_value.return_value = "rendered"
        sensor_config = {
            scrape.const.CONF_SELECT: ".value",
            CONF_VALUE_TEMPLATE: value_template,
        }
        arguments = {"query": "example"}

        def execute_extraction(target, *args):
            value_template.async_render_with_possible_json_value.assert_not_called()
            return target(*args)

        executor = AsyncMock(side_effect=execute_extraction)
        fake_hass = SimpleNamespace(async_add_executor_job=executor)

        result = await function._async_update_from_rest_data(
            fake_hass, data, sensor_config, arguments
        )

        assert result == "rendered"
        executor.assert_awaited_once_with(function._extract_value, data, sensor_config)
        value_template.async_render_with_possible_json_value.assert_called_once_with(
            "raw value", None, arguments
        )
