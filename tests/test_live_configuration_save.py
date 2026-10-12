"""Only reviewed request settings can suppress the ordinary save reload."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
    configuration_supports_live_update,
    is_live_subentry_update,
)
from tests.test_save_validation_reuse import _hass_with_agent, warm


@pytest.mark.parametrize(
    "updates,expected",
    [
        ({"max_tokens": 777}, True),
        ({"temperature": 0.3, "top_p": 0.8}, True),
        ({"chat_model": "gpt-4o", "max_tokens": 777}, False),
        ({"prompt": "A new prompt"}, False),
        ({"unknown_future_setting": True}, False),
    ],
)
def test_allow_list_checks_all_effective_changes(updates, expected):
    before = agent_config_defaults()
    assert (
        configuration_supports_live_update(
            before, {**before, **updates}, title_changed=False
        )
        is expected
    )
    assert not configuration_supports_live_update(
        before, {**before, **updates}, title_changed=True
    )


def test_default_materialization_is_unchanged_but_legacy_rewrites_require_reload():
    defaults = agent_config_defaults()
    assert configuration_supports_live_update({}, defaults, title_changed=False)
    assert not configuration_supports_live_update(
        {"prompt": "Historical stock prompt"}, defaults, title_changed=False
    )


@pytest.mark.parametrize(
    "updates,renamed,expected",
    [
        ({"max_tokens": 777}, False, True),
        ({"temperature": 0.3, "top_p": 0.8}, False, True),
        ({"max_tokens": 777}, True, False),
        ({"prompt": "New prompt"}, False, False),
        ({"max_tokens": 0}, False, None),
    ],
)
async def test_ordinary_save_marks_only_authorized_writes(
    monkeypatch, updates, renamed, expected
):
    monkeypatch.setattr(management_ui, "async_get_request_rules", AsyncMock(return_value=SimpleNamespace(snapshot=lambda: {"rules": []})))
    hass, entry, subentry = _hass_with_agent()
    # A non-reasoning model supports both sampling controls.
    subentry.data = normalize_agent_config(
        {**subentry.data, "chat_model": "gpt-4o", "api_mode": "chat_completions"}
    )
    _, revision = warm(subentry)
    previous = dict(subentry.data)
    original = hass.config_entries.async_update_subentry
    markers = []

    def record(*args, **kwargs):
        markers.append(is_live_subentry_update())
        return original(*args, **kwargs)

    monkeypatch.setattr(hass.config_entries, "async_update_subentry", record)
    monkeypatch.setattr(
        management_ui,
        "async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace(status=lambda: {})),
    )
    request = management_ui._ManagementRequest(
        hass,
        "admin",
        True,
        {
            "config": updates,
            "revision": revision,
            **({"title": "Renamed"} if renamed else {}),
        },
        entry.entry_id,
        subentry.subentry_id,
        entry,
        subentry,
    )
    result = await management_ui._async_save_configuration(request)
    assert result["valid"] is (expected is not None)
    defaults = agent_config_defaults()
    changed = {
        key: (
            previous.get(key, defaults.get(key)),
            subentry.data.get(key, defaults.get(key)),
        )
        for key in previous.keys() | subentry.data.keys()
        if previous.get(key, defaults.get(key))
        != subentry.data.get(key, defaults.get(key))
    }
    assert markers == ([] if expected is None else [expected]), changed
    assert not is_live_subentry_update(), (
        "the marker must not leak to a subsequent write"
    )


@pytest.mark.parametrize(
    "model,sampling",
    [
        ("gpt-4o", {}),
        ("gpt-4o", {"temperature": 0.0, "top_p": 0.0}),
        ("gpt-4o", {"temperature": 0.5, "top_p": 0.9}),
        ("gpt-5-mini", {"reasoning_effort": "high"}),
    ],
    ids=["omitted-legacy-settings", "explicit-zero", "explicit-sampling", "explicit-reasoning"],
)
async def test_unrelated_save_preserves_effective_provider_sampling_contract(
    monkeypatch, model, sampling
):
    """An unrelated change must not introduce, drop or change sampling kwargs."""
    from custom_components.extended_openai_conversation_responses.request import (
        build_provider_request_snapshot,
    )

    monkeypatch.setattr(
        management_ui,
        "async_get_request_rules",
        AsyncMock(return_value=SimpleNamespace(snapshot=lambda: {"rules": []})),
    )
    monkeypatch.setattr(
        management_ui,
        "async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace(status=lambda: {})),
    )
    hass, entry, subentry = _hass_with_agent()
    # Construct a legacy option set directly: normalization before the test
    # would hide the omitted-field regression.
    baseline = {
        **subentry.data,
        "chat_model": model,
        "api_mode": "chat_completions",
    }
    for key in ("temperature", "top_p", "reasoning_effort"):
        baseline.pop(key, None)
    baseline.update(sampling)
    subentry.data = baseline
    _, revision = warm(subentry)

    def effective(config):
        request = build_provider_request_snapshot(config, {}, tools_required=False)
        return {
            key: request.api_kwargs.get(key, "<omitted>")
            for key in ("temperature", "top_p", "reasoning_effort")
        }

    before = effective(subentry.data)
    for key, value in sampling.items():
        assert before[key] == value, (model, key, before)
    request = management_ui._ManagementRequest(
        hass, "admin", True,
        {"config": {"max_tokens": 777}, "revision": revision},
        entry.entry_id, subentry.subentry_id, entry, subentry,
    )
    saved = await management_ui._async_save_configuration(request)
    assert saved["valid"]
    after = effective(subentry.data)
    assert after == before, (sampling, before, after)
    assert subentry.data["max_tokens"] == 777
