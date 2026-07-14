"""Tests for ScrapeFunction using yaml definitions."""

from unittest.mock import AsyncMock, patch

import pytest

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
            from bs4 import BeautifulSoup

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
