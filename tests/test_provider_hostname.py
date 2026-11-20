"""Provider auto-detection uses only the parsed hostname and domain boundary."""

import pytest

from custom_components.extended_openai_conversation_responses.helpers import (
    effective_api_provider,
    is_azure_url,
    supports_openai_hosted_tools,
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


def test_apim_unified_model_route_respects_explicit_openai_selection():
    unified_url = "https://example.azure-api.net/team/llm/v1/"
    assert effective_api_provider("openai", unified_url) == "openai"
    assert effective_api_provider(None, unified_url) == "azure"
    assert effective_api_provider("azure", unified_url) == "azure"
    assert (
        effective_api_provider("openai", "https://example.azure-api.net/team/openai/v1/")
        == "azure"
    )


def test_hosted_web_search_accepts_documented_openai_residency_hosts():
    for region in ("ae", "au", "ca", "eu", "gb", "in", "jp", "kr", "sg", "us"):
        assert supports_openai_hosted_tools("openai", f"https://{region}.api.openai.com/v1/")
    for url in (
        "https://unknown.api.openai.com/v1",
        "https://jp.api.openai.com.example.net/v1",
        "https://jp.api.openai.com/other/v1",
        "https://jp.api.openai.com/v1?key=value",
        "http://jp.api.openai.com/v1",
    ):
        assert not supports_openai_hosted_tools("openai", url)
