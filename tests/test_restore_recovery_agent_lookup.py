"""Regression coverage for exact live-agent lookup during restore cleanup."""

from types import SimpleNamespace

from homeassistant.components import conversation
from homeassistant.components.conversation.const import DATA_COMPONENT

from custom_components.extended_openai_conversation_responses import restore_recovery


def test_active_agent_finds_exact_subentry_when_parent_mapping_points_elsewhere(
    monkeypatch,
) -> None:
    """Multiple conversation subentries cannot hide the restored live entity."""
    parent = SimpleNamespace(entry_id="entry-1")
    other = SimpleNamespace(
        entry=parent,
        subentry=SimpleNamespace(subentry_id="agent-other"),
    )
    target = SimpleNamespace(
        entry=parent,
        subentry=SimpleNamespace(subentry_id="agent-target"),
    )
    hass = SimpleNamespace(
        data={DATA_COMPONENT: SimpleNamespace(entities=(other, target))}
    )
    monkeypatch.setattr(conversation, "async_get_agent", lambda *_args: other)

    assert restore_recovery._active_agent(hass, "entry-1", "agent-target") is target
