"""Standalone HA child: imports no pytest, repository fixtures or test plugins."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import importlib
import importlib.metadata as metadata
import json
from pathlib import Path
import sys

DOMAIN = "extended_openai_conversation_responses"


async def until(predicate, timeout=120):
    async with asyncio.timeout(timeout):
        while not predicate():
            # Poll only the external controller/native HA state; ordering comes
            # from marker handshakes, not an assumed duration.
            await asyncio.sleep(0.05)


async def write(root, name, value):
    await asyncio.to_thread(
        (root / name).write_text, json.dumps(value), encoding="utf-8"
    )


def packages():
    return {d.metadata["Name"].lower(): d.version for d in metadata.distributions()}


async def late_controls(hass, phase):
    """A state-backed satellite fixture arrives through native HA state events."""
    from custom_components.extended_openai_conversation_responses.quiet_hours import (
        async_get_quiet_hours,
    )
    from homeassistant.util import dt as dt_util

    media, satellite = "media_player.isolated_voice", "assist_satellite.isolated_voice"
    seed = phase == "seed"
    suffix = "recover"
    late_media, late_satellite = (
        f"media_player.isolated_late_{suffix}",
        f"assist_satellite.isolated_late_{suffix}",
    )
    changes = []

    async def volume_set(call):
        changes.append(call.data["volume_level"])
        hass.states.async_set(
            call.data["entity_id"], "idle", {"volume_level": call.data["volume_level"]}
        )

    hass.services.async_register("media_player", "volume_set", volume_set)
    manager = await async_get_quiet_hours(hass)
    if seed:
        now = dt_util.now()
        hass.states.async_set(satellite, "idle")
        hass.states.async_set(media, "idle", {"volume_level": 0.55})
        await manager.async_update_config(
            {
                "enabled": True,
                "start": (now - timedelta(minutes=2)).strftime("%H:%M"),
                "end": (now + timedelta(minutes=30)).strftime("%H:%M"),
                "max_volume": 0.2,
                "overrides": {
                    satellite: {"media_player_entity_id": media},
                    **{
                        f"assist_satellite.isolated_late_{name}": {
                            "media_player_entity_id": f"media_player.isolated_late_{name}"
                        }
                        for name in ("recover",)
                    },
                },
            }
        )
    else:
        assert manager.active is not None
        assert hass.states.get(media) is None, (
            "Fixture appeared before initial reconciliation"
        )
        hass.states.async_set(satellite, "idle")
        hass.states.async_set(media, "idle", {"volume_level": 0.2})
        hass.states.async_set(late_satellite, "idle")
        hass.states.async_set(
            late_media, "idle", {"volume_level": 0.65 if phase == "recover" else 0.2}
        )
        await until(
            lambda: hass.states.get(late_media).attributes["volume_level"] == 0.2,
            timeout=360,
        )
        assert manager.active["controls"][late_media]["original_value"] == 0.65
    await until(lambda: hass.states.get(media).attributes["volume_level"] == 0.2)
    assert manager.active["controls"][media]["original_value"] == 0.55
    assert changes == ([0.2] if phase in ("seed", "recover") else []), changes
    return {
        "late_satellite_reconciliations": 1 if phase == "recover" else 0,
        "volume_original": 0.55,
        "volume_effect": 0.2,
    }


async def exercise(hass, entry, endpoint, expected):
    import voluptuous as vol

    from custom_components.extended_openai_conversation_responses.functions import (
        get_function,
    )
    from custom_components.extended_openai_conversation_responses.knowledge import (
        async_get_knowledge,
    )
    from custom_components.extended_openai_conversation_responses.memory import (
        async_get_memory,
    )
    from homeassistant.components import ai_task, conversation
    from homeassistant.core import Context
    from homeassistant.helpers import entity_registry as er

    result = await conversation.async_converse(
        hass=hass,
        text="Verify isolated runtime",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert result.response.error_code is None, result.response.as_dict()
    assert result.response.as_dict()["speech"]["plain"]["speech"] == expected
    task_sub = next(
        s for s in entry.subentries.values() if s.subentry_type == "ai_task_data"
    )
    entity_id = er.async_get(hass).async_get_entity_id(
        "ai_task", DOMAIN, task_sub.subentry_id
    )
    task = await ai_task.async_generate_data(
        hass,
        task_name="Isolated structured task",
        entity_id=entity_id,
        instructions="STRUCTURED_RUNTIME",
        structure=vol.Schema({vol.Required("count"): int}),
    )
    assert task.data == {"count": 0}
    for kind, config, expected_result in (
        (
            "rest",
            {
                "resource": endpoint + "/fact",
                "value_template": "{{ value_json.answer }}",
            },
            "retained-fact",
        ),
        (
            "scrape",
            {
                "resource": endpoint + "/page",
                "sensor": [{"name": "answer", "select": "#answer"}],
            },
            "retained-fact",
        ),
    ):
        function = get_function(kind)
        config = function.validate_schema({"type": kind, **config})
        actual = await function.execute(hass, config, {}, None, [])
        assert actual == expected_result, (kind, actual)
    sub = next(
        s for s in entry.subentries.values() if s.subentry_type == "conversation"
    )
    memory = await async_get_memory(hass, entry.entry_id, sub.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, sub.subentry_id)
    return memory, knowledge, sub


async def main(root, phase, endpoint):
    from homeassistant import bootstrap, runner
    from homeassistant.config_entries import SOURCE_USER, ConfigEntryState

    before = await asyncio.to_thread(packages)
    assert not any(name.startswith("pytest") for name in before), (
        "Test harness leaked into runtime"
    )
    sys.path.insert(0, str(root))
    if phase in ("recover-recorder-first", "recover-provider-first"):
        from homeassistant import loader
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.const import DATA_INSTANCE
        from homeassistant.core import HomeAssistant

        # Use HA's normal config-dictionary bootstrap so the observer can see
        # native readiness while setup is still in flight. No dependency or
        # installation helper is replaced and skip_pip remains False.
        hass = HomeAssistant(str(root))
        loader.async_setup(hass)
        hass.config.skip_pip = False
        boot = asyncio.create_task(
            bootstrap.async_from_config_dict(
                {"homeassistant": {"name": "Isolated acceptance"}, "recorder": {}}, hass
            )
        )
        await until(lambda: DATA_INSTANCE in hass.data)
        recorder = get_instance(hass)
        await until(lambda: recorder.engine is not None and recorder.is_alive())
        assert not recorder.async_db_ready.done()
        await write(
            root,
            "recorder-pending.json",
            {"worker_alive": True, "engine_created": True, "ready": False},
        )
        assert await boot is hass
        assert await recorder.async_db_ready
    else:
        hass = await bootstrap.async_setup_hass(
            runner.RuntimeConfig(config_dir=str(root), skip_pip=False)
        )
    assert hass is not None
    await hass.async_start()
    try:
        if phase == "seed":
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "name": "Isolated installation",
                    "api_key": "sk-isolated-runtime",
                    "base_url": endpoint + "/v1",
                    "skip_authentication": False,
                    "api_provider": "openai",
                },
            )
            assert result["type"] == "create_entry", result
        await until(lambda: len(hass.config_entries.async_entries(DOMAIN)) == 1)
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        if phase not in ("seed", "recover-provider-first"):
            expected = (
                ConfigEntryState.SETUP_RETRY
                if phase.startswith("recover")
                else ConfigEntryState.SETUP_ERROR
            )
            await until(lambda: entry.state is expected)
            flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            if phase == "auth":
                await until(
                    lambda: bool(
                        hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                    )
                )
                assert entry.reason == "API credentials are invalid or expired", (
                    entry.reason
                )
                flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                assert flows[0]["context"]["source"] == "reauth"
                await write(
                    root,
                    "observed.json",
                    {"state": entry.state.value, "reason": entry.reason},
                )
                await until(lambda: (root / "release").exists())
                # Credential failure must not magically heal merely because the
                # endpoint becomes reachable. A native reauth flow is required.
                assert entry.state is ConfigEntryState.SETUP_ERROR
                assert (
                    len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1
                )
                result = await hass.config_entries.flow.async_configure(
                    flows[0]["flow_id"], {"api_key": "sk-isolated-restored"}
                )
                assert (
                    result["type"] == "abort"
                    and result["reason"] == "reauth_successful"
                ), result
            else:
                assert not flows, "Transient unavailability incorrectly started reauth"
                await write(root, "observed.json", {"state": entry.state.value})
        await until(lambda: entry.state is ConfigEntryState.LOADED)
        await hass.async_block_till_done()
        module = importlib.import_module("custom_components." + DOMAIN)
        assert (root / "custom_components" / DOMAIN) in Path(
            module.__file__
        ).resolve().parents
        memory, knowledge, sub = await exercise(
            hass, entry, endpoint, "Isolated runtime healthy"
        )
        controls = await late_controls(hass, phase)
        marker = root / "retained.json"
        if phase == "seed":
            await memory.async_add(
                "user:isolated-owner", "retained-private-note", "acceptance", "explicit"
            )
            source = await knowledge.async_create(
                "Retained manual", "acceptance", "retained-source-text"
            )
            await write(
                root,
                "retained.json",
                {
                    "entry": entry.entry_id,
                    "subentry": sub.subentry_id,
                    "source": source.source_id,
                },
            )
        else:
            saved = json.loads(await asyncio.to_thread(marker.read_text))
            assert (
                entry.entry_id == saved["entry"]
                and sub.subentry_id == saved["subentry"]
            )
            assert any(
                item.content == "retained-private-note"
                for item in await memory.async_list("user:isolated-owner")
            )
            source = await knowledge.async_get(saved["source"])
            assert source is not None and source.content == "retained-source-text"
        rows = er_rows(hass, entry.entry_id)
        assert len({row.unique_id for row in rows}) == len(rows)
        after = await asyncio.to_thread(packages)
        await write(
            root,
            "result.json",
            {
                "phase": phase,
                "before": before,
                "after": after,
                "entry": entry.entry_id,
                "entities": len(rows),
                "controls": controls,
                "paths": [
                    "conversation",
                    "structured_ai_task",
                    "rest",
                    "scrape",
                    "memory",
                    "knowledge",
                ],
            },
        )
    finally:
        await hass.async_stop()


def er_rows(hass, entry_id):
    from homeassistant.helpers import entity_registry as er

    return er.async_entries_for_config_entry(er.async_get(hass), entry_id)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]))
