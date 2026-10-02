"""Provider auto-detection uses only the parsed hostname and domain boundary."""

import pytest

from custom_components.extended_openai_conversation_responses.helpers import (
    is_azure_url,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        (None, False),
        ("", False),
        ("https://example.openai.azure.com", True),
        ("https://EXAMPLE.OPENAI.AZURE.COM", True),
        ("https://Example.Services.AI.Azure.Com:8443/v1", True),
        ("https://example.azure-api.net:443", True),
        ("https://openai.azure.com", True),
        ("https://azure-api.net", True),
        ("https://services.ai.azure.com", True),
        ("https://proxy.example/v1", False),
        ("https://proxy.example/upstream/example.openai.azure.com/v1", False),
        ("https://proxy.example/v1?upstream=example.azure-api.net", False),
        ("https://proxy.example/v1#example.services.ai.azure.com", False),
        ("https://example.openai.azure.com.proxy.example/v1", False),
        ("https://example.azure-api.net.proxy.example", False),
        ("https://example.services.ai.azure.com.proxy.example", False),
        ("https://notopenai.azure.com", False),
        ("https://example.openai.azure.com@proxy.example/v1", False),
        ("example.openai.azure.com", False),
        ("https://[broken", False),
    ],
)
def test_hostname_provider_classification(url, expected):
    assert is_azure_url(url) is expected
