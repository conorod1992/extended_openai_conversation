"""Scrape templates through persisted tools, runtime dispatch and real HA HTTP parsing."""

import asyncio
from collections import Counter
from copy import deepcopy
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    delayed_tools,
    management_loading_performance,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from homeassistant.helpers.template import Template
from tests.test_remote_response_bounds import _FakeResponse, _FakeSession


@pytest.fixture
async def execute_scrape(hass, monkeypatch):
    for name in (
        "async_migrate_integration",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
        "async_setup_debug_ui",
        "async_setup_management_ui",
        "async_setup_delayed_tools",
    ):
        monkeypatch.setattr(integration, name, AsyncMock())
    monkeypatch.setattr(integration, "setup_provider_credentials_websocket", Mock())
    for name in ("async_setup_cached_debug_ui", "async_setup_cached_management_ui"):
        monkeypatch.setattr(management_loading_performance, name, AsyncMock())
    await integration.async_setup(hass, {})
    delayed_tools._install_execution_hook()
    hass.loop = asyncio.get_running_loop()
    hass.is_stopping = False
    hass.config.legacy_templates = False
    hass.config_entries.async_get_entry.return_value = None
    agent = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = hass
    agent.entity_id = "conversation.scrape_test"
    agent.entry = SimpleNamespace(entry_id="entry")
    agent._effective_guest_policy = Mock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    agent.should_run_in_background = lambda _delay: False
    response = _FakeResponse(b'<p class="value" data-literal="{{ untouched }}">42</p>')
    session = _FakeSession(response)
    monkeypatch.setattr(web, "async_get_clientsession", lambda *_a, **_kw: session)

    async def execute(config, arguments=None):
        raw = _tool(config)
        validate_function_tools([raw])
        agent.subentry = SimpleNamespace(
            subentry_id="agent", data={CONF_FUNCTION_TOOLS: yaml.safe_dump([raw])}
        )
        tool = agent._configured_function_tools_from_data(agent.subentry.data)[0]
        result = await agent._execute_function_tool(
            tool,
            SimpleNamespace(
                id="call", tool_name="scrape_test", tool_args=arguments or {}
            ),
            None,
            [],
        )
        return result.tool_result["result"]

    return execute, session


def _tool(config):
    return {
        "spec": {
            "name": "scrape_test",
            "description": "Read a page",
            "parameters": {
                "type": "object",
                "properties": {
                    key: {"type": "string"} for key in ("url", "text", "name")
                },
            },
        },
        "function": config,
    }


def _literal_config():
    return {
        "type": "scrape",
        "resource": "https://example.com/page",
        "sensor": [{"select": ".value"}],
    }


async def test_literal_and_omitted_fields(execute_scrape):
    execute, session = execute_scrape
    config = _literal_config()
    assert await execute(config) == "42"
    config.update(
        payload="{{ untouched }}", headers={"X-Literal": "plain"}, params={"q": "plain"}
    )
    config["sensor"][0]["attribute"] = "data-literal"
    assert await execute(config) == "{{ untouched }}"
    args, request = session.calls[-1]
    assert request["data"] == "{{ untouched }}"
    assert request["headers"] == {"X-Literal": "plain"}
    assert request["params"] == {"q": "plain"}
    assert "https://example.com/page" in args


async def test_each_template_renders_once_with_arguments_and_ha_context(
    execute_scrape, hass, monkeypatch
):
    execute, session = execute_scrape
    config = {
        "type": "scrape",
        "resource_template": "{{ url }}",
        "payload_template": "{{ text }}",
        "method": "POST",
        "headers": {"X-Text": "{{ text }}", "X-State": "{{ states('sensor.test') }}"},
        "params": {"q": "{{ text }}", "flag": "{{ true }}"},
        "sensor": [
            {"select": ".value", "name": "{{ name }}", "value_template": "{{ text }}"}
        ],
        "value_template": "{{ extracted }}",
    }
    before = deepcopy(config)
    counts = Counter()
    original = Template.async_render

    def render(self, *args, **kwargs):
        counts[self.template] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Template, "async_render", render)
    for text in ("{{ should_stay_literal }}", "second execution"):
        counts.clear()
        assert (
            await execute(
                config,
                {
                    "url": "https://example.com/{{ literal }}",
                    "text": text,
                    "name": "extracted",
                },
            )
            == text
        )
        assert counts == {
            "{{ url }}": 1,
            "{{ text }}": 4,
            "{{ states('sensor.test') }}": 1,
            "{{ true }}": 1,
            "{{ name }}": 1,
            "{{ extracted }}": 1,
        }
        args, request = session.calls[-1]
        assert "https://example.com/{{ literal }}" in args
        assert request["data"] == text
        assert request["headers"] == {"X-Text": text, "X-State": "on"}
        assert request["params"] == {"q": text, "flag": "true"}
    assert config == before


@pytest.mark.parametrize(
    "field",
    [
        "resource_template",
        "payload_template",
        "headers",
        "params",
        "name",
        "sensor_value",
        "value_template",
    ],
)
async def test_invalid_syntax_rejected_by_production_validation(execute_scrape, field):
    config = _literal_config()
    _set_template(config, field, "{{ broken")
    with pytest.raises(AgentConfigError, match="configuration is invalid for scrape"):
        await execute_scrape[0](config)
    assert not execute_scrape[1].calls


def _set_template(config, field, value):
    if field == "resource_template":
        config.pop("resource", None)
    if field in ("headers", "params"):
        config[field] = {"test": value}
    elif field in ("name", "sensor_value"):
        config["sensor"][0]["name" if field == "name" else "value_template"] = value
    else:
        config[field] = value


@pytest.mark.parametrize(
    "field",
    [
        "resource_template",
        "payload_template",
        "headers",
        "params",
        "name",
        "sensor_value",
        "value_template",
    ],
)
async def test_runtime_template_errors_are_tool_errors(execute_scrape, field):
    config = _literal_config()
    _set_template(config, field, "{{ missing.attribute }}")
    result = await execute_scrape[0](config)
    assert result["status"] == "error"
    assert "missing" in result["error"]


async def test_json_value_context_and_literal_selector(execute_scrape):
    execute, _session = execute_scrape
    config = _literal_config()
    config["sensor"][0].update(
        select='[data-literal="{{ untouched }}"]',
        value_template='{{ {"answer": value_json} | to_json }}',
    )
    config["value_template"] = "{{ value_json.answer }}"
    assert await execute(config) == "42"


def test_published_scrape_examples_validate(hass):
    root = Path(__file__).resolve().parents[1]
    count = 0
    for relative in (
        "docs/functions/scrape.mdx",
        "docs/functions/function-types.md",
        "docs/functions/composite.mdx",
        "examples/function/kakao_bus/README.md",
        "tests/fixtures/functions/scrape_example.yaml",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        blocks = (
            [source]
            if relative.endswith(".yaml")
            else re.findall(r"```yaml\n(.*?)```", source, re.S)
        )
        for block in blocks:
            for tool in _scrape_tools(yaml.safe_load(block)):
                web.ScrapeFunction().validate_schema(tool)
                count += 1
    assert count >= 7


def _scrape_tools(value):
    if isinstance(value, dict):
        if value.get("type") == "scrape":
            yield {
                key: item for key, item in value.items() if key != "response_variable"
            }
        else:
            for item in value.values():
                yield from _scrape_tools(item)
    elif isinstance(value, list):
        for item in value:
            yield from _scrape_tools(item)
