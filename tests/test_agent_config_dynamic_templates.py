"""Dynamic context templates validate before a Home Assistant instance exists."""

from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CURRENT_DATETIME_TEMPLATE,
    CONF_EXPOSED_ENTITIES_TEMPLATE,
)


def test_dynamic_context_templates_validate_without_home_assistant() -> None:
    values = {
        CONF_CURRENT_DATETIME_TEMPLATE: "Year {{ now().year }}",
        CONF_EXPOSED_ENTITIES_TEMPLATE: "Count {{ 2 + 2 }}",
    }
    normalized = normalize_agent_config(values)
    assert {key: normalized[key] for key in values} == values
