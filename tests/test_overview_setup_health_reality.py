"""Overview Setup & Health must be recomputed from current runtime facts."""

from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses import (
    management_setup_health,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_WEB_SEARCH,
    DEFAULT_PROMPT,
    MEMORY_MODE_MANUAL,
    MEMORY_MODE_OFF,
)


def test_setup_health_recomputes_live_state_instead_of_reusing_stale_facts(
    monkeypatch,
) -> None:
    """Changing live/runtime inputs must change the next Overview projection."""
    exposed = iter([0, 3])
    monkeypatch.setattr(
        management_setup_health, "_exposed_entity_count", lambda _hass: next(exposed)
    )
    monkeypatch.setattr(
        management_setup_health,
        "_function_health",
        lambda _options: {
            "usable_count": 0,
            "invalid_count": 0,
            "isolatable": True,
            "validation_error": None,
        },
    )
    monkeypatch.setattr(
        management_setup_health,
        "configuration_guidance_snapshot",
        lambda _entry, options: {
            "effective_api_mode": options.get(CONF_API_MODE, "auto"),
            "web_search": {
                "available": bool(options.get(CONF_WEB_SEARCH, False)),
                "reason": None,
            },
        },
    )
    monkeypatch.setattr(
        management_setup_health,
        "configured_lifecycle",
        lambda subentry: {
            "model": subentry.data.get(CONF_CHAT_MODEL, "gpt-test"),
            "status": "supported",
            "shutdown_at": None,
            "shutdown_reached": False,
        },
    )
    monkeypatch.setattr(
        management_setup_health,
        "confirmed_retirement_failure",
        lambda *_args: False,
    )

    entry = SimpleNamespace(
        entry_id="entry-1",
        data={},
        runtime_data=None,
    )
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        data={
            CONF_PROMPT: DEFAULT_PROMPT,
            CONF_CHAT_MODEL: "gpt-test",
            CONF_API_MODE: "auto",
            CONF_MEMORY_MODE: MEMORY_MODE_OFF,
            CONF_KNOWLEDGE_ENABLED: False,
            CONF_WEB_SEARCH: False,
        },
    )

    first = management_setup_health.build_setup_health_facts(
        object(),
        entry,
        subentry,
        memory_available=True,
        knowledge_source_count=0,
        knowledge_available=True,
        is_admin=True,
    )
    assert first["provider_runtime"]["client_loaded"] is False
    assert first["prompt_state"] == "starter"
    assert first["exposed_entity_count"] == 0
    assert first["memory"]["mode"] == MEMORY_MODE_OFF
    assert first["knowledge"]["enabled"] is False
    assert first["web_search"]["enabled"] is False

    entry.runtime_data = object()
    subentry.data = {
        **subentry.data,
        CONF_PROMPT: "Custom live instructions",
        CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
        CONF_KNOWLEDGE_ENABLED: True,
        CONF_WEB_SEARCH: True,
        CONF_API_MODE: "responses",
    }

    second = management_setup_health.build_setup_health_facts(
        object(),
        entry,
        subentry,
        memory_available=False,
        knowledge_source_count=4,
        knowledge_available=True,
        is_admin=False,
    )
    assert second["provider_runtime"]["client_loaded"] is True
    assert second["provider_runtime"]["configured_api_mode"] == "responses"
    assert second["prompt_state"] == "custom"
    assert second["exposed_entity_count"] == 3
    assert second["memory"] == {"mode": MEMORY_MODE_MANUAL, "available": False}
    assert second["knowledge"] == {
        "enabled": True,
        "source_count": 4,
        "available": True,
    }
    assert second["web_search"]["enabled"] is True
    assert second["web_search"]["effective_api_mode"] == "responses"
    assert second["can_manage"] is False
