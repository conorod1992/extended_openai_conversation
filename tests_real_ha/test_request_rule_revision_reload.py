"""An unrelated configuration save must not invalidate a same-tab rule revision."""

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.request_rules import (
    async_get_request_rules,
)
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_request_rules_script_semantics import _local


async def test_rule_revision_survives_configuration_save_and_entry_reload(hass):
    agent = await _agent(hass)
    entry, subentry = agent.entry, agent.subentry
    rules = agent._request_rules
    await rules.async_create(_local([{"set_conversation_response": "First"}]))
    revision = rules.revision()
    original_data = await rules.async_backup_data()

    result = await management_ui.async_management_command(
        hass,
        None,
        True,
        {
            "section": "configuration",
            "action": "save",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "config": {"prompt": "An unrelated prompt edit"},
            "revision": management_ui._agent_config_revision(
                subentry.data, subentry.title
            ),
        },
    )
    assert result["valid"]
    await hass.async_block_till_done()
    reloaded = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    assert reloaded is not rules
    assert await reloaded.async_backup_data() == original_data
    assert reloaded.revision() == revision
    await reloaded.async_create(
        _local([{"set_conversation_response": "Second"}], phrase="second"),
        expected_revision=revision,
    )
