"""Prompt rendering retains useful diagnostics without logging private source."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    render_effective_prompt,
)

SECRET = "FABRICATED-prompt-log-private-42"
SOURCE = '{% set private_token = "' + SECRET + '" %}Hello {{ missing }}'


@pytest.mark.parametrize(
    "field", ["prompt", "current_datetime_template", "exposed_entities_template"]
)
def test_prompt_render_warning_omits_unrendered_source(hass, caplog, field):
    options = agent_config_defaults()
    options["prompt"] = "Static public prompt"
    options["current_datetime_enabled"] = field == "current_datetime_template"
    options["exposed_entities_enabled"] = field == "exposed_entities_template"
    options[field] = SOURCE
    agent_config_snapshot(options)
    result = render_effective_prompt(
        hass,
        options,
        exposed_entities=[],
        current_device_id=None,
        user_input=SimpleNamespace(),
        skills=[],
        memory_scope_available=False,
    )
    assert SECRET not in result.text
    assert SECRET not in caplog.text
    assert "EOAI private template diagnostic" in caplog.text
    assert any(record.levelname == "WARNING" for record in caplog.records)


@pytest.mark.parametrize(
    "field", ["prompt", "current_datetime_template", "exposed_entities_template"]
)
@pytest.mark.parametrize("failure", [False, True])
def test_production_prompt_builder_retains_safe_diagnostics(
    hass, caplog, field, failure
):
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )
    from custom_components.extended_openai_conversation_responses.public_errors import (
        sanitize_template_error,
    )
    from homeassistant.exceptions import TemplateError

    options = agent_config_defaults()
    options["prompt"] = "Static public prompt"
    options["current_datetime_enabled"] = field == "current_datetime_template"
    options["exposed_entities_enabled"] = field == "exposed_entities_template"
    source = (
        SOURCE.replace("{{ missing }}", "{{ missing.value }}") if failure else SOURCE
    )
    options[field] = source
    agent_config_snapshot(options)
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.subentry = SimpleNamespace(subentry_id="agent-review", data=options)
    entity.skill_manager = SimpleNamespace(get_all_skills=lambda: [])
    entity._knowledge = None
    entity._guest_mode = None
    context = SimpleNamespace(device_id=None)
    if failure:
        with pytest.raises(TemplateError) as caught:
            entity._build_system_prompt([], context, SimpleNamespace())
        assert SECRET not in str(sanitize_template_error(caught.value))
    else:
        result = entity._build_system_prompt([], context, SimpleNamespace())
        assert SECRET not in result
    assert SECRET not in caplog.text
    logs = [
        r
        for r in caplog.records
        if "EOAI private template diagnostic" in r.getMessage()
    ]
    assert logs
    assert all(r.name == "homeassistant.helpers.template" for r in logs)
    assert any(r.levelname == ("ERROR" if failure else "WARNING") for r in logs)


def test_scoped_existing_tool_protection_control(hass, caplog):
    from custom_components.extended_openai_conversation_responses.template_diagnostics import (
        private_rendering_diagnostics,
    )
    from homeassistant.helpers.template import Template

    with private_rendering_diagnostics():
        assert SECRET not in Template(SOURCE, hass).async_render({}, parse_result=False)
    assert SECRET not in caplog.text
    assert "EOAI private template diagnostic" in caplog.text
    caplog.clear()
    assert SECRET not in Template(SOURCE, hass).async_render({}, parse_result=False)
    assert SECRET in caplog.text
