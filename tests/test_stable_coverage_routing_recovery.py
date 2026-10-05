"""Second concentrated stable-CI coverage pass for recovery and routing edges."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    intercom,
    model_lifecycle,
    skill_transactions,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.intercom import (
    IntercomManager,
)


def _patch_intercom_registries(monkeypatch, *, entities, devices, areas) -> None:
    entity_registry = SimpleNamespace(async_get=entities.get)
    device_registry = SimpleNamespace(async_get=devices.get)
    area_registry = SimpleNamespace(async_get_area=areas.get)
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entity_registry)
    monkeypatch.setattr(intercom.dr, "async_get", lambda _hass: device_registry)
    monkeypatch.setattr(intercom.ar, "async_get", lambda _hass: area_registry)


def test_intercom_same_alias_for_same_destination_is_not_ambiguous(
    hass, monkeypatch
) -> None:
    """A device and area alias may safely collapse to the same satellite set."""
    manager = IntercomManager(hass)
    catalog = {
        "satellites": [
            {
                "id": "assist_satellite.kitchen",
                "name": "Kitchen Voice",
                "aliases": [],
                "device_id": "device-kitchen",
                "area_id": "area-kitchen",
            }
        ],
        "devices": [
            {"id": "device-kitchen", "name": "Kitchen", "aliases": []},
        ],
        "areas": [
            {"id": "area-kitchen", "name": "Kitchen", "aliases": []},
        ],
        "floors": [],
        "labels": [],
    }
    _patch_intercom_registries(
        monkeypatch,
        entities={
            "assist_satellite.kitchen": SimpleNamespace(labels=set()),
        },
        devices={
            "device-kitchen": SimpleNamespace(labels=set()),
        },
        areas={
            "area-kitchen": SimpleNamespace(labels=set(), floor_id=None),
        },
    )

    assert manager.resolve_named_target("the kitchen", catalog=catalog) == {
        "device_ids": ["device-kitchen"],
        "name": "Kitchen",
    }


def test_intercom_same_alias_for_different_destinations_is_rejected(
    hass, monkeypatch
) -> None:
    manager = IntercomManager(hass)
    catalog = {
        "satellites": [
            {
                "id": "assist_satellite.one",
                "name": "One",
                "aliases": [],
                "device_id": "device-one",
                "area_id": "area-one",
            },
            {
                "id": "assist_satellite.two",
                "name": "Two",
                "aliases": [],
                "device_id": "device-two",
                "area_id": "area-two",
            },
        ],
        "devices": [
            {"id": "device-one", "name": "Shared", "aliases": []},
            {"id": "device-two", "name": "Device Two", "aliases": []},
        ],
        "areas": [
            {"id": "area-one", "name": "Area One", "aliases": []},
            {"id": "area-two", "name": "Shared", "aliases": []},
        ],
        "floors": [],
        "labels": [],
    }
    _patch_intercom_registries(
        monkeypatch,
        entities={
            "assist_satellite.one": SimpleNamespace(labels=set()),
            "assist_satellite.two": SimpleNamespace(labels=set()),
        },
        devices={
            "device-one": SimpleNamespace(labels=set()),
            "device-two": SimpleNamespace(labels=set()),
        },
        areas={
            "area-one": SimpleNamespace(labels=set(), floor_id=None),
            "area-two": SimpleNamespace(labels=set(), floor_id=None),
        },
    )

    with pytest.raises(HomeAssistantError, match="Ambiguous Broadcast destination"):
        manager.resolve_named_target("Shared", catalog=catalog)


def test_intercom_ambiguous_alias_with_no_effective_satellites_is_rejected(
    hass, monkeypatch
) -> None:
    manager = IntercomManager(hass)
    catalog = {
        "satellites": [],
        "devices": [{"id": "device-empty", "name": "Empty", "aliases": []}],
        "areas": [{"id": "area-empty", "name": "Empty", "aliases": []}],
        "floors": [],
        "labels": [],
    }
    _patch_intercom_registries(
        monkeypatch,
        entities={},
        devices={},
        areas={},
    )

    with pytest.raises(HomeAssistantError, match="Ambiguous Broadcast destination"):
        manager.resolve_named_target("Empty", catalog=catalog)


def test_intercom_alias_memberships_include_device_area_floor_and_labels(
    hass, monkeypatch
) -> None:
    """Membership projection uses entity, device and area metadata together."""
    manager = IntercomManager(hass)
    catalog = {
        "satellites": [
            {
                "id": "assist_satellite.kitchen",
                "name": "Kitchen Voice",
                "aliases": [],
                "device_id": "device-kitchen",
                "area_id": "area-kitchen",
            }
        ],
        "devices": [{"id": "device-kitchen", "name": "Kitchen Device", "aliases": []}],
        "areas": [{"id": "area-kitchen", "name": "Kitchen Area", "aliases": []}],
        "floors": [{"id": "floor-downstairs", "name": "Shared", "aliases": []}],
        "labels": [{"id": "label-shared", "name": "Shared", "aliases": []}],
    }
    _patch_intercom_registries(
        monkeypatch,
        entities={
            "assist_satellite.kitchen": SimpleNamespace(labels={"label-entity"}),
        },
        devices={
            "device-kitchen": SimpleNamespace(labels={"label-shared"}),
        },
        areas={
            "area-kitchen": SimpleNamespace(
                labels={"label-area"},
                floor_id="floor-downstairs",
            ),
        },
    )

    # Floor and label resolve to the same one satellite, so the shared alias is safe.
    assert manager.resolve_named_target("Shared", catalog=catalog) == {
        "floor_ids": ["floor-downstairs"],
        "name": "Shared",
    }


def test_intercom_named_target_handles_whole_home_missing_and_whitespace(hass) -> None:
    manager = IntercomManager(hass)
    empty = {
        "satellites": [],
        "devices": [],
        "areas": [],
        "floors": [],
        "labels": [],
    }

    assert manager.resolve_named_target("  whole   home  ", catalog=empty) == {
        "whole_home": True,
        "name": "whole   home",
    }
    assert manager.resolve_named_target("nowhere", catalog=empty) is None


@pytest.mark.asyncio
async def test_get_intercom_rejects_removed_integration(hass) -> None:
    hass.data[f"{DOMAIN}.removed"] = True

    with pytest.raises(HomeAssistantError, match="has been removed"):
        await intercom.async_get_intercom(hass)


@pytest.mark.asyncio
async def test_get_intercom_reuses_existing_manager(hass, monkeypatch) -> None:
    manager = SimpleNamespace(async_initialize=Mock())
    manager.async_initialize = pytest.importorskip("unittest.mock").AsyncMock()
    hass.data[intercom.DATA_KEY] = manager

    result = await intercom.async_get_intercom(hass)

    assert result is manager
    manager.async_initialize.assert_awaited_once()


def _first_install_transaction(tmp_path):
    installed = tmp_path / "installed"
    installed.mkdir()
    root = tmp_path / ".staging"
    staged = root / "candidate"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text("NEW")
    target = installed / "owned"
    backup = root / f"owned.backup-{'b' * 32}"
    journal = skill_transactions.prepare_transaction(root, target, backup, staged)
    return installed, root, target, staged, backup, journal


def test_skill_recovery_removes_interrupted_first_install_candidate(tmp_path) -> None:
    installed, _root, target, staged, _backup, journal = _first_install_transaction(
        tmp_path
    )
    staged.rename(target)

    skill_transactions.recover_transaction(journal, installed)

    assert not target.exists()
    assert not staged.exists()
    assert not journal.exists()


def test_skill_recovery_rejects_changed_first_install_candidate(tmp_path) -> None:
    installed, _root, target, staged, _backup, journal = _first_install_transaction(
        tmp_path
    )
    staged.rename(target)
    (target / "SKILL.md").unlink()
    target.rmdir()
    target.mkdir()
    (target / "SKILL.md").write_text("EXTERNAL")

    with pytest.raises(
        HomeAssistantError, match="target is not the interrupted candidate"
    ):
        skill_transactions.recover_transaction(journal, installed)


def test_skill_recovery_requires_known_good_previous_version(tmp_path) -> None:
    installed = tmp_path / "installed"
    installed.mkdir()
    root = tmp_path / ".staging"
    target = installed / "owned"
    target.mkdir()
    (target / "SKILL.md").write_text("OLD")
    staged = root / "candidate"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text("NEW")
    backup = root / f"owned.backup-{'c' * 32}"
    journal = skill_transactions.prepare_transaction(root, target, backup, staged)

    # Simulate loss of both the original target and the expected backup before
    # recovery sees the prepared journal.
    for child in target.iterdir():
        child.unlink()
    target.rmdir()

    with pytest.raises(HomeAssistantError, match="Known-good Skill is unavailable"):
        skill_transactions.recover_transaction(journal, installed)


def test_skill_recovery_rejects_symlinked_staging_root(tmp_path) -> None:
    installed = tmp_path / "installed"
    installed.mkdir()
    real_root = tmp_path / "real-staging"
    real_root.mkdir()
    link = tmp_path / ".staging"
    link.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(HomeAssistantError, match="staging root"):
        skill_transactions.recover_transactions(link, installed)


def test_model_lifecycle_override_lookup_handles_unloaded_and_routed_rules(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    entry = SimpleNamespace(entry_id="entry")
    subentry = SimpleNamespace(subentry_id="agent")
    hass = SimpleNamespace(data={})

    assert model_lifecycle._override_model_is_still_configured(
        hass, entry, subentry, "gpt-old"
    )

    manager = SimpleNamespace(
        _initialized=True,
        snapshot=lambda: {
            "rules": [
                {
                    "enabled": True,
                    "action_type": "model_routing",
                    "action": {
                        "reset": False,
                        "model": "gpt-old",
                    },
                }
            ]
        },
    )
    hass.data[request_rules._MANAGERS] = {("entry", "agent"): manager}
    assert model_lifecycle._override_model_is_still_configured(
        hass, entry, subentry, "gpt-old"
    )

    manager.snapshot = lambda: {
        "rules": [
            {
                "enabled": False,
                "action_type": "model_routing",
                "action": {"reset": False, "model": "gpt-old"},
            }
        ]
    }
    assert not model_lifecycle._override_model_is_still_configured(
        hass, entry, subentry, "gpt-old"
    )


def test_entity_request_model_prefers_current_run_override() -> None:
    entity = SimpleNamespace(
        _usage=SimpleNamespace(
            current_run=lambda: SimpleNamespace(models=["gpt-base", "gpt-override"])
        ),
        subentry=SimpleNamespace(data={CONF_CHAT_MODEL: "gpt-configured"}),
    )

    assert model_lifecycle._entity_request_model(entity) == "gpt-override"

    entity._usage.current_run = lambda: None
    assert model_lifecycle._entity_request_model(entity) == "gpt-configured"


def test_entity_retirement_wrappers_ignore_partial_entity(monkeypatch) -> None:
    record = Mock()
    clear = Mock()
    monkeypatch.setattr(model_lifecycle, "record_retirement_failure", record)
    monkeypatch.setattr(model_lifecycle, "clear_retirement_failure", clear)

    assert (
        model_lifecycle.record_entity_retirement_failure(
            SimpleNamespace(), RuntimeError("failure")
        )
        is None
    )
    model_lifecycle.clear_entity_retirement_failure(SimpleNamespace())

    record.assert_not_called()
    clear.assert_not_called()


def test_entity_retirement_wrappers_use_active_request_model(monkeypatch) -> None:
    hass = SimpleNamespace()
    entry = SimpleNamespace(entry_id="entry")
    subentry = SimpleNamespace(
        subentry_id="agent",
        title="Assistant",
        data={CONF_CHAT_MODEL: "gpt-configured"},
    )
    entity = SimpleNamespace(
        hass=hass,
        entry=entry,
        subentry=subentry,
        _usage=SimpleNamespace(
            current_run=lambda: SimpleNamespace(models=["gpt-routed"])
        ),
    )
    record = Mock(return_value="retired")
    clear = Mock()
    monkeypatch.setattr(model_lifecycle, "record_retirement_failure", record)
    monkeypatch.setattr(model_lifecycle, "clear_retirement_failure", clear)

    assert (
        model_lifecycle.record_entity_retirement_failure(
            entity, RuntimeError("failure")
        )
        == "retired"
    )
    model_lifecycle.clear_entity_retirement_failure(entity)

    assert record.call_args.kwargs["model"] == "gpt-routed"
    assert record.call_args.kwargs["configured_model"] == "gpt-configured"
    clear.assert_called_once_with(
        hass,
        entry_id="entry",
        subentry_id="agent",
        model="gpt-routed",
    )


def test_sync_all_model_lifecycles_visits_every_entry(monkeypatch) -> None:
    entries = [SimpleNamespace(entry_id="one"), SimpleNamespace(entry_id="two")]
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _domain: entries)
    )
    sync = Mock()
    monkeypatch.setattr(model_lifecycle, "sync_entry_model_lifecycle", sync)

    model_lifecycle.sync_all_model_lifecycles(hass)

    assert [call.args[1] for call in sync.call_args_list] == entries
