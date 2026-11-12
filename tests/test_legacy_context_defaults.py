"""Existing agents retain runtime context semantics across Management operations."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_config as config,
    backup,
    management_ui as ui,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.management_loading_performance import (
    _snapshot_normalized_configuration,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    render_effective_prompt,
)
from tests.test_request_rules import manager as rule_manager

FIELDS = ("current_datetime_enabled", "exposed_entities_enabled")


def _raw(datetime_value, entities_value):
    raw = {
        "chat_model": "gpt-4.1",
        "functions": "[]",
        "prompt": "Instructions",
        "current_datetime_template": "DATE CONTEXT",
        "exposed_entities_template": "ENTITY CONTEXT",
    }
    for key, value in zip(FIELDS, (datetime_value, entities_value), strict=True):
        if value is not None:
            raw[key] = value
    return raw


def _assert_effective(values, raw):
    assert {key: values[key] for key in FIELDS} == {
        key: raw.get(key, False) for key in FIELDS
    }


@pytest.mark.parametrize("datetime_value", [None, True, False])
@pytest.mark.parametrize("entities_value", [None, True, False])
def test_legacy_reads_edits_and_transfers_match_runtime(
    hass, datetime_value, entities_value
):
    raw = _raw(datetime_value, entities_value)
    original = deepcopy(raw)
    persisted = config.normalize_persisted_agent_config(raw)
    snapshot = config.agent_config_snapshot(raw)
    candidate = config.merge_agent_config(raw, {"max_tokens": 900})
    for values in (persisted, snapshot, candidate):
        _assert_effective(values, raw)
    saved = config.project_agent_config_updates(raw, {"max_tokens": 900}, candidate)
    assert saved == {**raw, "max_tokens": 900}
    sections = {
        section.key
        for section in render_effective_prompt(
            hass,
            candidate,
            exposed_entities=[],
            current_device_id=None,
            user_input=SimpleNamespace(text="hello"),
            skills=[],
        ).sections
    }
    assert ("current_datetime_context" in sections) == raw.get(FIELDS[0], False)
    assert ("exposed_entities_context" in sections) == raw.get(FIELDS[1], False)
    portable = ui._export_agent(SimpleNamespace(data=raw))
    imported = ui._parse_import_document(portable)["config"]
    for transferred in (
        imported,
        backup.export_configuration_snapshot(raw),
        backup.private_configuration_snapshot(raw),
        backup.recoverable_configuration_snapshot(
            {**raw, "future_extension": {"exact": [False, None]}}
        ),
    ):
        for key in FIELDS:
            assert (key in transferred) == (key in raw)
        _assert_effective(config.agent_config_snapshot(transferred), raw)
    recovered = backup.private_configuration_snapshot(
        {**raw, "future_extension": {"exact": [False, None]}}
    )
    assert recovered["future_extension"] == {"exact": [False, None]}
    _assert_effective(_snapshot_normalized_configuration(imported, validated=True), raw)
    assert raw == original


def test_new_agent_and_explicit_reset_keep_enabled_defaults():
    for values in (config.agent_config_defaults(), config.normalize_agent_config({})):
        assert all(values[key] is True for key in FIELDS)
    raw = _raw(None, None)
    updates = {key: config.agent_config_defaults()[key] for key in FIELDS}
    reset = config.project_agent_config_updates(
        raw, updates, config.merge_agent_config(raw, updates)
    )
    assert all(reset[key] is True for key in FIELDS)


@pytest.mark.parametrize("value", [None, True, False])
async def test_management_read_preview_save_duplicate_import_and_reset(
    hass, monkeypatch, value
):
    raw = _raw(value, value)
    raw["future_extension"] = {"keep": [False, None, "unchanged"]}
    original = deepcopy(raw)
    subentry = SimpleNamespace(
        subentry_id="agent", subentry_type="conversation", title="Old", data=raw
    )
    entry = SimpleNamespace(
        entry_id="entry", domain=DOMAIN, data={}, subentries={"agent": subentry}
    )
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))
    rules = await rule_manager()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    preview = AsyncMock(return_value={"preview": True})
    monkeypatch.setattr(ui, "_async_preview_effective_request", preview)

    def save(_entry, target, *, data, title=None):
        target.data = deepcopy(data)
        if title is not None:
            target.title = title

    hass.config_entries.async_update_subentry.side_effect = save

    async def command(action, **values):
        return await ui.async_management_command(
            hass,
            "admin",
            True,
            {
                "section": "configuration",
                "action": action,
                "entry_id": "entry",
                "subentry_id": "agent",
                **values,
            },
        )

    read = await command("get")
    _assert_effective(read["config"], raw)
    assert all(read["defaults"][key] is True for key in FIELDS)
    for action in ("validate", "request_preview", "prompt_preview"):
        result = await command(action, config={"max_tokens": 901})
        if action == "validate":
            _assert_effective(result["config"], raw)
        else:
            _assert_effective(preview.await_args.args[3], raw)
    assert subentry.data == original
    result = await command(
        "update", config={"max_tokens": 901}, revision=read["revision"]
    )
    _assert_effective(result["config"], raw)
    assert subentry.data == {**original, "max_tokens": 901}
    await command("duplicate")
    duplicate = hass.config_entries.async_add_subentry.call_args.args[1]
    for key in FIELDS:
        assert duplicate.data.get(key, False) == raw.get(key, False)
        assert (key in duplicate.data) == (key in raw)
    exported = await command("export")
    imported_preview = await command("import_preview", document=exported["document"])
    _assert_effective(imported_preview["config"], raw)
    current = await command("get")
    await command(
        "import",
        document=exported["document"],
        confirm=True,
        revision=current["revision"],
    )
    # Also check the seeded fast cache used immediately after import.
    _assert_effective((await command("get"))["config"], raw)
    current = await command("get")
    reset = await command(
        "update", config={key: True for key in FIELDS}, revision=current["revision"]
    )
    assert all(reset["config"][key] is True for key in FIELDS)
    assert all(subentry.data[key] is True for key in FIELDS)
